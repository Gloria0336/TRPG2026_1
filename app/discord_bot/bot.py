"""Discord bot: slash commands and dice-button flow."""
from __future__ import annotations

import asyncio
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

from ..ai import orchestrator, prompts
from ..config import settings
from ..content import currency, director, monsters, scenario
from ..db import store
from ..engine import combat, resolution
from ..engine.combat import CombatError
from ..engine.types import CostType, IntentTier
from ..logging_setup import finish_recording, get_logger, start_recording
from ..state import campaigns, game_state
from ..world import location_registration, movement as world_movement
from . import dice_animations, embeds, i18n
from .views import ChoiceView, RollView

log = get_logger("bot")


# Movement verbs the dispatcher treats as travel intents when paired with a
# location-like target. "travel" is already the AI's canonical token (see prompts),
# but the parser regularly returns less-marked verbs ("go"/"walk"/"follow"/"head"
# in English; "前往"/"走"/"去"/"離開"/"出發"/"進入" in Chinese). We don't widen this
# into "approach"/"reach" — those are too easily confused with non-travel intents.
_TRAVEL_VERBS: frozenset[str] = frozenset({
    "travel", "go", "walk", "move", "leave", "exit", "follow",
    "enter", "depart", "head", "ride", "approach",
    "前往", "走", "去", "離開", "出發", "進入", "跟隨", "移動", "出門",
})

_QUEST_ACCEPT_WORDS: frozenset[str] = frozenset({
    "accept", "accepted", "take the job", "take this job", "i will help", "i'll help",
    "i will find", "i'll find", "count on me",
    "接下", "接受", "我接", "我會幫", "我幫", "交給我", "我會找到", "我會救",
    "我願意", "包在我", "承接", "答應",
})


def _looks_like_travel(gs, intent) -> bool:
    """True when the player's intent reads as 'move to a place'.

    Trigger conditions (any one is enough):
    - intent.action is a known travel verb AND intent.target resolves to a known
      location (canonical or alias).
    - intent.action is a travel verb AND intent.target equals the current location
      name — meaning "I leave here" (handled by _begin_travel's leave branch).
    - intent.action is the literal "travel" — the original explicit channel.

    Does NOT fire when the target is an in-scene person/object even if the verb
    is "follow" (e.g. "I follow the bartender" — bartender is a person, not a
    place). _begin_travel guards on store.find_location anyway.
    """
    if not intent.target:
        return False
    action = (intent.action or "").strip().lower()
    if action == "travel":
        return True
    if action not in _TRAVEL_VERBS:
        return False
    # Target resolves to a registered location → travel
    if store.find_location(intent.target) is not None:
        return True
    # Target equals the current location name → "leave here"
    here = store.get_entity_by_id(gs.current_location_id)
    if here:
        names = [here.get("name", "")] + list(here.get("aliases") or [])
        t = intent.target.strip().lower()
        if any(n and (t in n.lower() or n.lower() in t) for n in names):
            return True
    # Travel verb + a target that is NOT a present in-scene person/object/creature →
    # treat as travel to an emergent place; _begin_travel auto-registers it via
    # resolve_or_register_location. Without this, "走回鎮上" (鎮上 not yet a registered
    # location) fell through to a no-roll narrative beat that moved the fiction ("you
    # arrive in town") without moving party_location_id — stranding the engine at the
    # old location (the "teleport back" bug).
    ent = store.find_by_ref(gs.current_location_id, intent.target)
    if ent is not None and ent.get("kind") != "location":
        return False
    return True

intents = discord.Intents.default()
intents.message_content = False

bot = commands.Bot(command_prefix="!trpg ", intents=intents, help_command=None)


@bot.event
async def on_ready():
    # Resume the latest per-campaign DB (migrating a pre-refactor single save first).
    campaigns.migrate_legacy_if_needed()
    if campaigns.resume_latest() is None:
        store.init_db()
    if game_state.get_state() is None:
        saved = game_state.GameState.load()
        if saved:
            game_state.set_state(saved)
    try:
        command_names = ", ".join(sorted(cmd.name for cmd in bot.tree.get_commands()))
        if settings.discord_guild_id:
            guild = discord.Object(id=int(settings.discord_guild_id))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"[bot] synced guild slash commands to {settings.discord_guild_id}: {command_names}")
        else:
            await bot.tree.sync()
            print(f"[bot] synced global slash commands: {command_names}")
    except Exception as exc:  # noqa: BLE001
        print(f"[bot] slash-command sync failed: {exc}")
    print(f"[bot] logged in as {bot.user}; ready.")


def _state_for_channel(channel_id: int) -> game_state.GameState | None:
    return game_state.active_campaign_for_channel(channel_id)


def _is_allowed_channel_id(channel_id: int | None) -> bool:
    allowed = settings.parsed_discord_allowed_channel_ids
    return not allowed or (channel_id in allowed)


def _disallowed_channel_message() -> str:
    allowed = sorted(settings.parsed_discord_allowed_channel_ids)
    channel_list = "、".join(_channel_ref(channel_id) for channel_id in allowed)
    if channel_list:
        return f"這個 bot 只能在指定頻道使用：{channel_list}"
    return "這個 bot 目前沒有允許使用的 Discord 頻道。"


async def _ensure_allowed_channel(interaction: discord.Interaction) -> bool:
    if _is_allowed_channel_id(interaction.channel_id):
        return True
    await interaction.response.send_message(_disallowed_channel_message(), ephemeral=True)
    return False


def _channel_ref(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if isinstance(channel_id, int) and channel_id > 0 else "未知頻道"


def _start_block_message(channel_id: int) -> str | None:
    gs = game_state.active_campaign()
    if gs is None or not game_state.has_discord_channel_binding(gs):
        return None
    if gs.channel_id == channel_id:
        return "這個頻道已經有正在進行的戰役，不能再次使用 `/start`。"
    return f"目前已經有正在進行的戰役（{_channel_ref(gs.channel_id)}），請先到該頻道完成或結束該戰役後再使用 `/start`。"


def _finish_target_for_channel(channel_id: int) -> tuple[game_state.GameState | None, str | None]:
    gs = game_state.active_campaign_for_channel(channel_id)
    if gs:
        return gs, None

    gs = game_state.active_campaign()
    if gs is None:
        return None, "這裡目前沒有進行中的戰役。"
    if not game_state.has_discord_channel_binding(gs):
        return gs, None
    return None, f"目前戰役綁定在 {_channel_ref(gs.channel_id)}，請到該頻道使用 `/finish`。"


def _mention_for(gs: game_state.GameState, actor_id: str) -> str | None:
    for uid, pid in gs.players.items():
        if pid == actor_id:
            return f"<@{uid}>"
    return None


def _display_name(user) -> str:
    return getattr(user, "display_name", None) or getattr(user, "global_name", None) or getattr(user, "name", None) or str(user)


async def _persist(gs: game_state.GameState) -> None:
    try:
        gs.save()
    except OSError:
        pass


async def _send_dice_animation(channel, natural: int | None) -> None:
    if natural is None:
        return
    file = dice_animations.animation_file(natural)
    if file is None:
        log.warning(
            "dice animation missing: set_id=%s natural=%s",
            dice_animations.ACTIVE_DICE_ANIMATION_SET_ID,
            natural,
        )
        return
    await channel.send(file=file)


async def _narrate_into_log(gs: game_state.GameState, result, *, allow_quest: bool = True) -> str:
    if allow_quest:
        prose, quest_seed = await orchestrator.narrate_with_quest(gs, result)
    else:
        prose, quest_seed = await orchestrator.narrate(gs, result), None
    if gs.event_log:
        gs.set_narration(gs.event_log[-1].id, prose)
    # A time-cost result means in-fiction time passed — advance the world clock so the
    # day actually progresses (and stale "清晨" summaries stop overriding it).
    if getattr(getattr(result, "cost", None), "type", None) is CostType.TIME:
        gs.advance_time()
    await _apply_entity_updates(gs, prose, result)
    if quest_seed is not None:
        await _handle_quest_seed(gs, result, quest_seed)
    return prose


async def _handle_quest_seed(gs: game_state.GameState, result, quest_seed) -> None:
    seed = quest_seed.model_dump() if hasattr(quest_seed, "model_dump") else dict(quest_seed)
    dedupe = seed.get("dedupe_key") or (
        f"{gs.current_location_id}:{seed.get('giver') or result.target_name}:"
        f"{seed.get('title_hint') or seed.get('objective_hint') or seed.get('premise')}"
    ).strip().lower()
    status = "awaiting_check" if seed.get("acceptance_mode") == "requires_check" else "available"
    event_id = gs.event_log[-1].id if gs.event_log else None
    quest = store.upsert_quest_seed(
        dedupe_key=dedupe,
        seed={**seed, "dedupe_key": dedupe},
        source_event_id=event_id,
        scene_id=gs.current_location_id,
        giver=seed.get("giver") or result.target_name or "",
        status=status,
        visibility="summary",
        detail_state="pending_agent",
    )
    gs.bump()

    async def run_agent(qid: str, seed_data: dict) -> None:
        try:
            details, detail_state = await orchestrator.build_quest_details(gs, seed_data)
            store.set_quest_details(qid, details, detail_state=detail_state)
            gs.bump()
        except Exception as exc:  # noqa: BLE001
            log.warning("quest agent background task failed (%s): %s", type(exc).__name__, exc)
            degraded = store.fallback_quest_details(seed_data)
            store.set_quest_details(qid, degraded, detail_state="details_degraded")
            gs.bump()

    asyncio.create_task(run_agent(quest["id"], quest["seed"]), name=f"quest-agent-{quest['id']}")


def _is_pc_ref(gs: game_state.GameState, ref: str | None) -> bool:
    """True when a free-text entity reference resolves to a player character. PCs live on
    Character (+ their conditions), never in the narrative-entity registry, so the
    extractor must never register or mutate them as NPCs (trace bug: a PC's name was
    mention-tallied toward becoming an NPC entity).

    Matches both the English Character.name ("Bram Ironwood") and its canonical localized
    form (i18n "布拉姆·鐵木") — the extractor reads the Chinese narration, so the ref is
    usually the localized name. Narrative NPCs like 老佩林 are DB entities, not Characters,
    so they are never in gs.pcs() — no false skips."""
    if not ref or not ref.strip():
        return False
    t = ref.strip().lower()
    for pc in gs.pcs():
        candidates = [pc.name, i18n.name(pc.name)]
        if pc.name:
            candidates.append(pc.name.split()[0])  # English first name ("Bram")
        for n in candidates:
            nl = (n or "").strip().lower()
            if nl and (t == nl or t in nl or nl in t):
                return True
    return False


def _grant_recipient_id(gs: game_state.GameState, ref: str | None, fallback_actor_id: str | None) -> str | None:
    if ref and ref.strip():
        t = ref.strip().lower()
        for c in gs.characters.values():
            candidates = [c.id, c.name, i18n.name(c.name)]
            if c.name:
                candidates.append(c.name.split()[0])
            for name in candidates:
                n = (name or "").strip().lower()
                if n and (t == n or t in n or n in t):
                    return c.id
    return fallback_actor_id if fallback_actor_id in gs.characters else None


async def _apply_entity_updates(gs: game_state.GameState, prose: str, result) -> None:
    """After each narration: pull entity-state deltas (LLM extraction, offline-safe)
    and apply them, then recompute the dynamic scene summary so the next turn reads
    current presence/state instead of the static blurb."""
    scope = gs.current_location_id
    threshold = settings.mention_promote_threshold
    try:
        extraction = await orchestrator.extract_entity_states(gs, prose, result)
        for delta in extraction.actionable():
            d = delta.model_dump()
            ref = d.get("entity_ref") or d.get("register_name")
            # PCs are never narrative entities — drop any delta about them before it can
            # mention-tally into (or mutate) an NPC record. Authoritative guard: holds
            # even offline / when the model ignores the prompt instruction.
            if _is_pc_ref(gs, ref):
                log.info("entity delta skipped (PC ref, never an NPC entity): scope=%s ref=%s", scope, ref)
                continue
            existing = store.find_by_ref(scope, ref) if ref else None
            # A brand-new entity the AI just invented in prose → debounce: only promote
            # after it has been named `threshold` times (design: prevent drift-by-bloat
            # AND prevent the world snapping back by never recording it). Updates to an
            # entity that already exists stay immediate.
            if existing is None and d.get("register_kind"):
                name = d.get("register_name") or d.get("entity_ref")
                kind = d.get("register_kind")
                count = store.record_mention(scope, name, kind)
                if count == 0:
                    continue  # already a registered entity/location elsewhere
                if count >= threshold:
                    if kind == "location":
                        ent_id = await location_registration.promote_location_mention_with_card(
                            scope, name, gs
                        )
                    else:
                        ent_id = store.promote_mention(scope, name, kind)
                    log.info("entity promoted after %d mention(s): scope=%s name=%s kind=%s id=%s",
                             count, scope, name, kind, ent_id)
                else:
                    log.info("entity mention tallied (%d/%d): scope=%s name=%s kind=%s",
                             count, threshold, scope, name, kind)
                continue
            # Narrated hostile that the extractor reported WITHOUT a register_kind (the
            # gpt-4o-mini failure mode behind the "attack didn't start combat" bug): a foe
            # who just turned on the party must materialise NOW so the next /action can
            # trigger combat — debouncing it for `threshold` mentions would leave the
            # engine with no entity to fight. Bypass the tally and register immediately.
            if existing is None and d.get("disposition") in {"hostile", "attack"}:
                kind = monsters.infer_combat_kind(ref)
                ent_id = store.register_combatant(
                    scope, ref, kind, disposition=d["disposition"],
                    aliases=d.get("aliases") or [], note=d.get("note") or "",
                )
                if ent_id:
                    log.info("entity auto-registered (narrated hostile, no debounce): "
                             "scope=%s ref=%s kind=%s id=%s", scope, ref, kind, ent_id)
                continue
            ent_id = store.apply_delta(scope, d)
            if ent_id:
                log.info("entity delta applied: scope=%s entity=%s %s",
                         scope, ent_id, delta.model_dump(exclude_none=True))
        for grant in extraction.acquired_items():
            if currency.looks_like_currency(grant.item_name) and not currency.parse_currency_grant(
                grant.item_name, grant.quantity
            ):
                log.info(
                    "currency grant skipped (unquantified or missing denomination): %s",
                    grant.model_dump(exclude_none=True),
                )
                continue
            recipient_id = _grant_recipient_id(
                gs,
                grant.recipient_ref,
                getattr(result, "actor_id", None),
            )
            if not recipient_id:
                log.info("item grant skipped (no recipient): %s", grant.model_dump(exclude_none=True))
                continue
            try:
                saved = store.grant_item(
                    recipient_id,
                    grant.item_name,
                    quantity=grant.quantity,
                    category=grant.category,
                    event_id=getattr(result, "event_id", None),
                )
            except ValueError as exc:
                log.info("item grant skipped (%s): %s", exc, grant.model_dump(exclude_none=True))
                continue
            actor = gs.characters.get(recipient_id)
            if actor:
                actor.inventory = store.project_inventory(recipient_id)
            log.info(
                "item grant applied: actor=%s item=%s qty=%s source=%s",
                recipient_id,
                saved.get("name"),
                saved.get("quantity"),
                grant.source_ref,
            )
        # Lasting change to the place itself (not tied to a single entity) → persist on
        # the location so it survives revisits and shows in the dynamic summary.
        if extraction.location_note and store.append_location_state_note(scope, extraction.location_note):
            log.info("location state note added: scope=%s note=%s",
                     scope, extraction.location_note[:80])
    except Exception as exc:  # noqa: BLE001 — continuity layer must not break play
        log.warning("entity update failed (%s): %s", type(exc).__name__, exc)
    # Rolling scene digest: refresh every `rolling_summary_every` beats so plot that has
    # scrolled out of the event window is still remembered (compose_scene_summary reads
    # current_summary back). Cadence-gated to keep the extra cheap-model call occasional.
    try:
        every = max(1, settings.rolling_summary_every)
        if settings.rolling_summary_enabled and len(gs.event_log) % every == 0:
            await orchestrator.update_rolling_summary(gs)
    except Exception as exc:  # noqa: BLE001
        log.warning("rolling summary refresh failed (%s): %s", type(exc).__name__, exc)
    gs.bump()


def _action_from_display(pc, label: str) -> str:
    for action in pc.actions:
        if label in (action.name, i18n.action(action.name)):
            return action.name
    return label


def _target_from_display(enemies, label: str) -> str:
    for enemy in enemies:
        if label in (enemy.name, i18n.name(enemy.name)):
            return enemy.name
    return label


def _character_name(gs: game_state.GameState, actor_id: str | None) -> str:
    actor = gs.characters.get(actor_id or "")
    return i18n.name(actor.name) if actor else "未知角色"


def _text_accepts_quest(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low or word in text for word in _QUEST_ACCEPT_WORDS)


def _quest_matches_text(quest: dict, text: str) -> bool:
    scoped = store.list_quests(scene_id=quest.get("scene_id"))
    if len(scoped) == 1:
        return True
    seed = quest.get("seed") or {}
    fields = (
        quest.get("giver"), seed.get("giver"), seed.get("title_hint"),
        seed.get("objective_hint"), seed.get("premise"),
    )
    return any(str(x or "") and str(x) in text for x in fields)


def _combat_entity_candidates(gs: game_state.GameState) -> list[dict]:
    return [
        e for e in gs.present_entities()
        if e.get("kind") in {"person", "creature"}
    ]


def _entity_combat_trigger_id(gs: game_state.GameState, target: str | None) -> str | None:
    if target:
        ent = store.find_by_ref(gs.current_location_id, target)
        if ent and ent.get("status") not in store._ABSENT_STATUSES and ent.get("kind") in {"person", "creature"}:
            return ent["id"]
    for ent in _combat_entity_candidates(gs):
        if ent.get("disposition") in {"hostile", "attack"}:
            return ent["id"]
    return None


_PRONOUN_TARGETS = {
    "他", "她", "它", "牠", "他們", "她們", "它們", "牠們", "對方", "敵人", "敵方",
    "them", "him", "her", "it",
}


def _resolve_attack_trigger(gs: game_state.GameState, intent) -> str | None:
    """Resolve an attack target to a combat-ready entity id, MATERIALISING one on demand
    when the target is a plausible but unregistered person/creature (e.g. attacking the
    tavern barkeep, who lives only in the location card). Falls back to the existing
    registered-entity / present-hostile lookup first; returns None when there's nothing
    sensible to fight (so dispatch degrades to a normal skill check)."""
    existing = _entity_combat_trigger_id(gs, intent.target)
    if existing:
        return existing
    target = (intent.target or "").strip()
    # A bare pronoun with no resolvable present hostile has no antecedent to spin up —
    # don't conjure an entity literally named "他". (Once a foe is registered, the
    # present-hostile scan in _entity_combat_trigger_id catches the pronoun above.)
    if not target or target.lower() in _PRONOUN_TARGETS:
        return None
    # A target that already resolves to a record we DIDN'T pick above is a non-combatant
    # (object/location) or an absent corpse — not a new fight.
    if store.find_by_ref(gs.current_location_id, target) is not None:
        return None
    # A place/exit reference is travel, never an attack target.
    if _looks_like_travel(gs, intent):
        return None
    kind = monsters.infer_combat_kind(target)
    disposition = monsters.infer_disposition(target)
    ent_id = store.register_combatant(
        gs.current_location_id, target, kind, disposition=disposition,
    )
    if ent_id:
        log.info("attack target materialised on demand: scope=%s target=%s kind=%s disp=%s id=%s",
                 gs.current_location_id, target, kind, disposition, ent_id)
    return ent_id


def _attack_ready_entities(gs: game_state.GameState) -> list[dict]:
    return [
        e for e in _combat_entity_candidates(gs)
        if e.get("disposition") == "attack"
    ]


def _sync_story_flags_for_accepted_quest(gs: game_state.GameState, quest: dict) -> None:
    seed = quest.get("seed") or {}
    details = quest.get("details") or {}
    hay = " ".join(str(x or "") for x in (
        quest.get("dedupe_key"), quest.get("giver"), seed.get("giver"),
        seed.get("title_hint"), seed.get("premise"), seed.get("objective_hint"),
        details.get("title"), details.get("objective"),
    )).lower()
    if gs.scene.id == "tavern" and (
        "perrin" in hay or "老佩林" in hay or "佩林" in hay or "caravan" in hay or "商隊" in hay
    ):
        gs.flags["accepted_quest"] = True


async def _try_accept_available_quest(channel, user, gs, pc, text: str) -> bool:
    if not _text_accepts_quest(text):
        return False
    candidates = [
        q for q in store.list_quests(scene_id=gs.current_location_id)
        if q.get("status") == "available" and _quest_matches_text(q, text)
    ]
    if not candidates:
        return False
    quest = store.accept_quest(candidates[-1]["id"])
    if quest is None:
        return False
    _sync_story_flags_for_accepted_quest(gs, quest)
    gs.begin_freeplay_action(pc.id)
    await _persist(gs)
    async with channel.typing():
        result = resolution.narrative_beat(
            gs, pc, text, target_name=quest.get("giver") or None, raw_text=text,
            hint="Describe the character accepting the quest plainly.",
        )
        prose = await _narrate_into_log(gs, result, allow_quest=False)
    await channel.send(embed=embeds.result_embed(result, prose))
    gs.complete_freeplay_action(pc.id)
    await _track_story(channel, gs)
    await _send_freeplay_turn_prompt(channel, gs)
    return True


def _maybe_apply_quest_check(gs: game_state.GameState, result) -> None:
    if result.success is not True:
        return
    changed = False
    for quest in store.list_quests(scene_id=gs.current_location_id):
        if quest.get("status") != "awaiting_check":
            continue
        seed = quest.get("seed") or {}
        check = str(seed.get("required_check") or "").lower()
        summary = str(result.summary or "").lower()
        target = str(result.target_name or "")
        giver = str(quest.get("giver") or seed.get("giver") or "")
        if check and check not in summary:
            continue
        if giver and target and giver not in target and target not in giver:
            continue
        if _text_accepts_quest(result.raw_text or ""):
            accepted = store.accept_quest(quest["id"])
            if accepted:
                _sync_story_flags_for_accepted_quest(gs, accepted)
        else:
            store.update_quest_status(quest["id"], "available")
        changed = True
    if changed:
        gs.bump()


async def _send_freeplay_turn_prompt(channel, gs: game_state.GameState) -> None:
    if gs.flags.get("over") or (gs.combat and gs.combat.active):
        return
    actor_id = gs.current_freeplay_actor_id()
    if actor_id:
        log.info("_send_freeplay_turn_prompt: actor=%s channel=%s", actor_id, getattr(channel, "id", None))
        await channel.send(f"➡️ 輪到 **{_character_name(gs, actor_id)}** 行動。請使用 `/action` 宣告你要做的事。")


async def _ensure_freeplay_turn(channel, gs: game_state.GameState, pc, user, continue_pending: bool) -> bool:
    if gs.combat and gs.combat.active:
        return True

    pending = gs.pending_freeplay_actor_id()
    if pending and pending != pc.id:
        await channel.send(f"⏳ 正在等待 **{_character_name(gs, pending)}** 完成目前的選項或擲骰。")
        return False
    if pending == pc.id and not continue_pending:
        await channel.send(f"⏳ {user.mention}，請先完成你目前的選項或擲骰，這一輪不能再宣告新的動作。")
        return False

    current = gs.current_freeplay_actor_id()
    if current and current != pc.id:
        await channel.send(f"⏳ 現在輪到 **{_character_name(gs, current)}**，{user.mention} 請等對方結束這一輪。")
        return False
    return True


@bot.event
async def on_message(message: discord.Message):
    return


async def process_action(channel, user, actor_id: str, text: str, continue_pending: bool = False) -> None:
    log.info("process_action: channel=%s user=%s actor=%s text=%r continue_pending=%s",
             channel.id, user.id, actor_id, text, continue_pending)
    if not _is_allowed_channel_id(channel.id):
        log.warning("process_action: disallowed channel=%s — dropping", channel.id)
        return
    gs = _state_for_channel(channel.id)
    if gs is None:
        log.warning("process_action: no active game in channel=%s — dropping", channel.id)
        return
    pc = gs.characters.get(actor_id)
    if pc is None:
        log.warning("process_action: actor_id=%s not found in gs.characters=%s", actor_id, list(gs.characters))
        return

    in_combat = bool(gs.combat and gs.combat.active)
    log.info("process_action: scene=%s pc=%s in_combat=%s", gs.scene.id, pc.name, in_combat)
    if in_combat:
        cur = combat.current_combatant(gs)
        if cur is None or cur.id != pc.id:
            who = i18n.name(cur.name) if cur else "沒有人"
            log.info("process_action: not %s's turn (current=%s) — blocking", pc.name, cur.name if cur else None)
            await channel.send(f"⏳ 現在是 **{who}** 的回合，{user.mention} 請稍等。")
            return
        if pc.is_down:
            log.info("process_action: %s is down → death save", pc.name)
            await _resolve_combat_action(channel, user, gs, pc, action_name=None, target_ref=None, death_save=True)
            return
    else:
        # If the player has an OPEN clarification thread, this /action is the
        # free-form reply to the prior GM follow-up — treat it like a
        # continuation (skip the "complete your previous option" block). The
        # reply text is recorded onto the thread only if we ask AGAIN (the tier-C
        # branch below pushes it as that round's player utterance); on convergence
        # it's simply consumed, so nothing to store here.
        clarif_open = gs.clarification_turn_count(pc.id) > 0
        if clarif_open:
            continue_pending = True
        if not await _ensure_freeplay_turn(channel, gs, pc, user, continue_pending):
            log.info("process_action: freeplay turn check rejected for %s", pc.name)
            return
        if not clarif_open and await _try_accept_available_quest(channel, user, gs, pc, text):
            log.info("process_action: accepted available quest via direct text")
            return

    clarification = gs.get_clarification(actor_id)
    async with channel.typing():
        intent, assessment = await orchestrator.interpret(
            gs, actor_id, text, clarification=clarification,
        )

    log.info("process_action: dispatch intent.tier=%s action=%s target=%s approach=%s is_attack=%s implausible=%s dc=%s",
             intent.tier.value, intent.action, intent.target, intent.approach, intent.is_attack, intent.implausible,
             assessment.final_dc if assessment else None)

    # False-premise guard (design §8.3 anti-talk): a message relying on gear the actor
    # lacks or a fact not in the scene gets an in-world redirect, NOT a menu that would
    # legitimise it. Does not consume the turn — the player can immediately try again.
    if intent.implausible:
        log.info("process_action → implausible redirect (action=%s target=%s)", intent.action, intent.target)
        await _send_implausible_redirect(channel, user, gs, pc, intent)
        return

    if intent.tier is IntentTier.B and intent.candidates:
        # B tier (clear goal, unclear method) still uses ChoiceView — these are
        # concrete method options the engine has narrowed down, not a GM follow-up.
        gs.clear_clarification(pc.id)  # converged enough to enumerate methods
        label_map = {i18n.text(x): x for x in intent.candidates}
        labels = list(label_map)

        async def on_choice(interaction: discord.Interaction, label: str):
            await interaction.response.edit_message(content=f"➡️ {i18n.name(pc.name)}：**{label}**", view=None)
            await process_action(channel, user, actor_id, label_map.get(label, label), continue_pending=True)

        if not in_combat:
            gs.begin_freeplay_action(pc.id)
            await _persist(gs)
        await channel.send(content=f"🤔 {user.mention}，你想怎麼做？", view=ChoiceView(user.id, labels, on_choice))
        return

    if intent.tier is IntentTier.C:
        # GM follow-up flow: post the parser's narrative question and lock the
        # actor's turn. The player's NEXT /action will be treated as a free-form
        # reply (continue_pending=True is set above when a thread is open) and
        # re-interpreted with the growing CLARIFICATION HISTORY in the prompt.
        #
        # Convergence cap: if we've already exchanged MAX_CLARIFICATION_TURNS
        # rounds without escaping tier C, give up — narrate a no-roll beat so
        # play moves on rather than looping forever.
        prior_turns = gs.clarification_turn_count(pc.id)
        if prior_turns >= game_state.GameState.MAX_CLARIFICATION_TURNS:
            log.info("process_action → C-tier cap (%d turns) → narrative fallback for %s",
                     prior_turns, pc.name)
            gs.clear_clarification(pc.id)
            await _begin_narrative(channel, user, gs, pc, intent)
            return

        question = i18n.text(intent.question) if intent.question else (
            "（請描述你想做的事，可以直接打字回覆，例如「我抓住他的衣領」）"
        )
        # Optional hints — show as plain text under the question, NOT as buttons,
        # so the player still types their own answer.
        hints = intent.options or []
        hint_line = ""
        if hints:
            hint_line = "\n💡 例如：" + "、".join(i18n.text(o) for o in hints[:3])

        gs.push_clarification(pc.id, text, question)
        if not in_combat:
            gs.begin_freeplay_action(pc.id)
        await _persist(gs)
        await channel.send(f"🎭 {question}{hint_line}\n（{user.mention} 直接 `/action` 回覆即可）")
        return

    # Tier A flowing through to resolution — the player's intent converged.
    if not in_combat:
        gs.clear_clarification(pc.id)

    if in_combat:
        log.info("process_action → _combat_declare")
        await _combat_declare(channel, user, gs, pc, intent, text)
    elif _looks_like_travel(gs, intent):
        log.info("process_action → _begin_travel target=%s (verb=%s)", intent.target, intent.action)
        await _begin_travel(channel, user, gs, pc, intent)
    elif intent.is_attack and scenario.scene_by_id(gs.scene.id) and scenario.scene_by_id(gs.scene.id).get("encounter"):
        log.info("process_action → _begin_scene_combat (attack triggered encounter)")
        gs.clear_pending_freeplay_action()
        await _begin_scene_combat(channel, gs)
    elif intent.is_attack and (trigger_entity_id := _resolve_attack_trigger(gs, intent)):
        trigger_ent = store.get_entity_by_id(trigger_entity_id)
        disposition = (trigger_ent or {}).get("disposition")
        if game_state.GameState.is_combat_hostile(disposition):
            log.info("process_action → _begin_entity_combat target=%s entity=%s (hostile)",
                     intent.target, trigger_entity_id)
            gs.clear_pending_freeplay_action()
            await _begin_entity_combat(channel, gs, trigger_entity_id)
        else:
            # Provocation ladder: attacking a non-hostile unit degrades its attitude one
            # rung (with an NPC reaction) instead of snapping into combat. Combat only
            # begins once it has been pushed to hostile (see _begin_provocation_beat).
            log.info("process_action → _begin_provocation_beat target=%s entity=%s disp=%s",
                     intent.target, trigger_entity_id, disposition)
            await _begin_provocation_beat(channel, user, gs, pc, intent, trigger_entity_id)
    elif intent.needs_check:
        log.info("process_action → _begin_check (out-of-combat check)")
        await _begin_check(channel, user, gs, pc, intent, assessment)
    else:
        log.info("process_action → _begin_narrative (trivial no-roll beat)")
        await _begin_narrative(channel, user, gs, pc, intent)


async def _begin_check(channel, user, gs, pc, intent, assessment) -> None:
    skill = resolution.normalize_approach(intent.approach or intent.action)
    dc = resolution.determine_dc(gs, intent, assessment)
    npc_mod, npc_disp = resolution.npc_dc_adjustment(gs, intent)
    dc_note = f"{prompts.disposition_label(npc_disp)} {npc_mod:+d}" if npc_mod else None
    log.info("_begin_check: pc=%s skill=%s dc=%s target=%s base=%s env=%s npc=%+d(%s)",
             pc.name, skill, dc, intent.target,
             assessment.base_dc if assessment else None,
             assessment.env_modifier if assessment else None,
             npc_mod, npc_disp or "—")
    label = f"{i18n.skill(skill)}檢定"
    if intent.target:
        label += f"：{i18n.text(intent.target)}"

    # §4.9 helper opt-in: only offered when the OTHER PC is proficient in this skill
    # (an untrained helper grants +0 — UX hides the button to avoid false hope).
    helper_user_id: int | None = None
    helper_pc_id: str | None = None
    for other_pc in gs.pcs():
        if other_pc.id == pc.id:
            continue
        if not other_pc.is_trained(skill):
            continue
        claim = gs.claim_for_pc(other_pc.id)
        if not claim:
            continue
        try:
            helper_user_id = int(claim["user_id"])
        except (TypeError, ValueError):
            helper_user_id = None
        if helper_user_id is not None:
            helper_pc_id = other_pc.id
            break

    view = RollView(
        user.id,
        on_roll=None,  # set after the view exists so the closure can capture it
        helper_user_id=helper_user_id,
        helper_pc_id=helper_pc_id,
        helper_label=f"我來協助 (+2)",
    )

    async def on_roll(interaction: discord.Interaction):
        log.info("_begin_check.on_roll: pc=%s helpers=%s rolling check", pc.name, view.helpers)
        result = resolution.resolve(gs, intent, assessment=assessment, helpers=list(view.helpers))
        await interaction.response.edit_message(content="🎲 擲骰中...", embed=None, view=None)
        await _send_dice_animation(channel, result.natural)
        prose = await _narrate_into_log(gs, result)
        await interaction.edit_original_response(content=None, embed=embeds.result_embed(result, prose))
        _maybe_apply_quest_check(gs, result)
        gs.complete_freeplay_action(pc.id)
        await _persist(gs)
        await _maybe_resolve_climax(channel, gs, result)
        if not gs.flags.get("over") and not (gs.combat and gs.combat.active):
            await _track_story(channel, gs)
        await _send_freeplay_turn_prompt(channel, gs)

    view.on_roll = on_roll

    gs.begin_freeplay_action(pc.id)
    await _persist(gs)
    await channel.send(embed=embeds.roll_prompt_embed(pc, label, dc, dc_note), view=view)


async def _begin_narrative(channel, user, gs, pc, intent) -> None:
    """Trivial / uncontested Tier-A action (design §8.2): no roll, just narrate the beat
    and advance the turn. The engine gate (resolution.requires_check) has already
    confirmed nothing is at stake here."""
    summary = intent.raw_text or intent.action or "敘事節拍"
    gs.begin_freeplay_action(pc.id)
    await _persist(gs)
    async with channel.typing():
        result = resolution.narrative_beat(
            gs, pc, summary, target_name=intent.target, raw_text=intent.raw_text,
        )
        prose = await _narrate_into_log(gs, result)
    await channel.send(embed=embeds.result_embed(result, prose))
    gs.complete_freeplay_action(pc.id)
    await _track_story(channel, gs)
    await _send_freeplay_turn_prompt(channel, gs)


async def _begin_provocation_beat(channel, user, gs, pc, intent, trigger_entity_id: str) -> None:
    """A player attack on a non-hostile unit (design: provocation ladder). No dice, no
    damage — step the target's attitude one rung toward hostility (friendly→neutral→hostile)
    and let the narrator dramatise the unit's reaction. The NEXT swing at a now-hostile unit
    is what _resolve_attack_trigger/dispatch routes into real combat."""
    before = (store.get_entity_by_id(trigger_entity_id) or {}).get("disposition") or "neutral"
    gs.begin_freeplay_action(pc.id)
    after = gs.provoke_entity(trigger_entity_id)
    target_name = (store.get_entity_by_id(trigger_entity_id) or {}).get("name") or intent.target
    if before in {"afraid", "cowed"} or after == before:
        # Submissive unit — it cowers / flees / begs rather than escalating.
        hint = (f"{target_name} 並不還手：受驚退避、求饒或試圖逃走，絕不轉為戰鬥。"
                "描述對方的畏縮反應與玩家攻擊落空/被閃避，不造成傷害。")
    elif after in {"hostile", "attack"}:
        hint = (f"{target_name} 被徹底激怒，態度轉為敵對、擺出戰鬥姿態並發出最後警告——"
                "但尚未交手。描述這一擊與對方翻臉的瞬間，不造成傷害、不擲骰。")
    else:
        hint = (f"{target_name} 對這次挑釁感到震驚與慍怒，出言警告或戒備後退，態度明顯轉壞。"
                "描述玩家的攻擊被閃過/格開，不造成傷害、不擲骰。")
    await _persist(gs)
    async with channel.typing():
        result = resolution.narrative_beat(
            gs, pc, intent.raw_text or intent.action or "挑釁",
            target_name=target_name, raw_text=intent.raw_text, hint=hint,
        )
        prose = await _narrate_into_log(gs, result)
    await channel.send(embed=embeds.result_embed(result, prose))
    gs.complete_freeplay_action(pc.id)
    await _track_story(channel, gs)
    await _send_freeplay_turn_prompt(channel, gs)


async def _send_implausible_redirect(channel, user, gs, pc, intent) -> None:
    """The message leaned on a false premise (gear the actor doesn't carry, or a fact not
    in the scene). Redirect in-world instead of offering a menu that legitimises it."""
    tgt = i18n.text(intent.target) if intent.target else None
    if tgt:
        msg = (f"⚠️ {user.mention}，{i18n.name(pc.name)} 身上並沒有能做到這件事的裝備或條件。"
               f"你想改用什麼方式處理「{tgt}」？（說出一個可行的做法）")
    else:
        msg = (f"⚠️ {user.mention}，這個行動依目前的處境無法成立"
               f"（你並沒有相關的裝備，現場也沒有這樣的條件）。換個可行的做法試試？")
    await channel.send(msg)


def _unregistered_location_target(name: str) -> dict:
    return {"id": None, "name": name.strip(), "kind": "location", "_unregistered": True}


def _resolve_travel_target(gs, target: str, *, register_unknown: bool = True) -> dict | None:
    """Map a free-text travel target to a location entity dict.

    Special case: when the target reads as the current location ("走出酒館" while
    standing in 酒館), it's a *leave* intent — we redirect to the location's
    natural outside (the first known location that isn't here, falling back to an
    auto-registered '外面'). Otherwise defer to the registry helper which
    auto-creates emergent destinations on demand.
    """
    if not target or not target.strip():
        return None
    here = store.get_entity_by_id(gs.current_location_id)
    if here:
        names = [here.get("name", "")] + list(here.get("aliases") or [])
        t = target.strip().lower()
        if any(n and (t == n.lower() or t in n.lower() or n.lower() in t) for n in names):
            # "leave here" — step out to the containing area (parent) when known, else the
            # nearest authored exit, else create a generic outside.
            parent_id = (here.get("flags") or {}).get("parent")
            if parent_id:
                parent = store.get_entity_by_id(parent_id)
                if parent:
                    return parent
            for other in store.get_locations():
                if other["id"] != here["id"]:
                    return other
            return store.resolve_or_register_location(f"{here['name']}外")
    known = store.find_location(target)
    if known is not None:
        return known
    if register_unknown:
        return store.resolve_or_register_location(target)
    return _unregistered_location_target(target)


def _travel_metrics(src_id: str, traversed: list[str], units: list[object]) -> dict:
    distance_km = 0.0
    time_h = 0.0
    prev = src_id
    for node in traversed:
        edge_km = world_movement.edge_distance(prev, node)
        speeds = [world_movement.unit_speed(unit, node) for unit in units]
        speed = min(speeds) if speeds else world_movement.DEFAULT_MOVEMENT_BASE_KMH
        distance_km += edge_km
        time_h += world_movement.edge_time_hours(prev, node, speed)
        prev = node
    speed_kmh = distance_km / time_h if time_h > 0 else 0.0
    return {"distance_km": distance_km, "time_h": time_h, "speed_kmh": speed_kmh}


def _plan_travel(src_id: str, dst_id: str, units: list[object] | None = None) -> dict:
    """Walk the world-graph path src→dst (store.travel_path) applying the access gate per
    node (design §6 hierarchy + §12 mixed gate). Returns:
      reached     — furthest node the party actually enters (dst, the node before a hard gate,
                    or None if the very next hop is hard-gated → party stays put);
      traversed   — node ids entered, in order; cost — day-stages spent;
      blocked     — hard-gated node id that halted travel (or None);
      warnings    — [{id, access}] for soft-gated nodes passed through;
      unreachable — True if dst is unknown / in a disconnected component (caller falls back).
    No teleport: distant targets are reached by routing through intermediates, paying cost."""
    units = units or []
    path = store.travel_path(src_id, dst_id)
    if path is None:
        return {"reached": None, "traversed": [], "cost": 0, "blocked": None,
                "warnings": [], "unreachable": True,
                "source_id": src_id,
                "distance_km": 0.0, "time_h": 0.0, "speed_kmh": 0.0}
    traversed: list[str] = []
    cost = 0
    warnings: list[dict] = []
    blocked: str | None = None
    for node in path:
        access = store.location_access(node)
        if access["gate"] == "hard":
            blocked = node
            break
        if access["gate"] == "soft":
            warnings.append({"id": node, "access": access})
        traversed.append(node)
        cost += store.location_travel_cost(node)
    metrics = _travel_metrics(src_id, traversed, units)
    return {"reached": traversed[-1] if traversed else None, "traversed": traversed,
            "cost": cost, "blocked": blocked, "warnings": warnings, "unreachable": False,
            "source_id": src_id,
            **metrics}


def _hard_block_message(blocked_id: str | None) -> str:
    blocked = store.get_entity_by_id(blocked_id) if blocked_id else None
    name = (blocked or {}).get("name") or "前方"
    access = store.location_access(blocked_id) if blocked_id else {}
    reason = (access.get("reason") or "").strip()
    if access.get("required_rank"):
        head = f"⛔ 你還沒有進入「{name}」的資格（需要 {access['required_rank']} 級）。"
    else:
        head = f"⛔ 「{name}」此刻無法進入。"
    return (head + reason).strip()


def _travel_notice(plan: dict) -> str:
    """Routing + soft-gate flavour shown before the destination scene opens."""
    bits: list[str] = []
    traversed = list(plan["traversed"])
    inter = traversed[:-1]  # intermediates the party passed through to get here
    names = [(store.get_entity_by_id(i) or {}).get("name", "") for i in inter]
    names = [n for n in names if n]
    if names:
        bits.append(f"🧭 你途經 {'、'.join(names)}")
    distance = float(plan.get("distance_km") or 0.0)
    time_h = float(plan.get("time_h") or 0.0)
    source_id = plan.get("source_id")
    pure_containment = bool(traversed and source_id and not plan.get("blocked") and distance <= 0)
    prev = source_id
    for node in traversed:
        if world_movement.edge_kind(str(prev), node) != "containment":
            pure_containment = False
            break
        prev = node
    if pure_containment:
        here = (store.get_entity_by_id(str(source_id)) or {}).get("name") or str(source_id)
        dest = (store.get_entity_by_id(traversed[-1]) or {}).get("name") or traversed[-1]
        bits.append(f"🧭 你走出了{here}，來到{dest}。")
    if distance > 0 and time_h > 0:
        bits.append(f"🧭 路程約 {_fmt_amount(distance)} km，耗時約 {_fmt_hours(time_h)}。")
    for w in plan["warnings"]:
        loc = store.get_entity_by_id(w["id"]) or {}
        reason = (w["access"].get("reason") or "").strip() or "此地危險，風險自負。"
        bits.append(f"⚠️ {loc.get('name', '')}：{reason}")
    if plan["blocked"]:
        b = store.get_entity_by_id(plan["blocked"]) or {}
        bits.append(f"⛔ 再往前的「{b.get('name', '')}」擋下了去路，你止步於此。")
    return "\n".join(bits)


def _fmt_amount(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _fmt_hours(hours: float) -> str:
    if hours < 1:
        minutes = max(1, round(hours * 60))
        return f"{minutes} 分鐘"
    return f"{_fmt_amount(hours)} 小時"


async def _begin_travel(channel, user, gs, pc, intent) -> None:
    """Free travel (design §6: location is first-class state). Resolve/register the
    destination, pathfind through the hierarchical world graph applying the access gate,
    move the party to the furthest reachable node, then open it — so the structured location
    follows the fiction instead of being left behind, and distance/danger actually bite."""
    loc = None
    try:
        loc = _resolve_travel_target(gs, intent.target or "", register_unknown=False)
    except Exception as exc:  # noqa: BLE001 — continuity layer must not break play
        log.warning("_begin_travel: resolve location failed (%s): %s", type(exc).__name__, exc)
    if loc is None:
        # Couldn't pin a destination — fall back to a plain no-roll beat.
        await _begin_narrative(channel, user, gs, pc, intent)
        return
    if loc.get("_unregistered") or not store.get_location_card(loc.get("id")):
        try:
            loc, _card = await location_registration.register_location_with_card(
                location_registration.LocationRegistrationRequest(
                    requested_name=loc["name"],
                    canonical_name=None if loc.get("_unregistered") else loc.get("name"),
                    source="player_travel",
                    state=gs,
                    location_id=loc.get("id"),
                    aliases=list(loc.get("aliases") or []),
                    authored_notes=loc.get("notes") or "",
                    player_text=getattr(intent, "raw_text", "") or "",
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("_begin_travel: location card registration failed (%s): %s",
                        type(exc).__name__, exc)
            if loc.get("_unregistered"):
                loc = store.resolve_or_register_location(loc["name"])
            if loc is None:
                await _begin_narrative(channel, user, gs, pc, intent)
                return
    src_id = gs.current_location_id
    if loc["id"] == src_id:
        # Resolver returned 'here' — the special-case logic should have redirected, but on an
        # alias collision we'd otherwise loop. Narrate in-place so play still advances.
        log.info("_begin_travel: target resolved to current location %s — narrating in-place", loc["id"])
        await _begin_narrative(channel, user, gs, pc, intent)
        return

    travel_units = gs.pcs() or [pc]
    plan = _plan_travel(src_id, loc["id"], travel_units)
    if plan["unreachable"]:
        # Emergent / disconnected place (no graph adjacency) — reach it directly, design says
        # player-invented places stay freely reachable. Single hop, its own travel_cost.
        traversed = [loc["id"]]
        plan = {"reached": loc["id"], "traversed": [loc["id"]],
                "cost": store.location_travel_cost(loc["id"]), "blocked": None,
                "warnings": [{"id": loc["id"], "access": store.location_access(loc["id"])}]
                if store.location_access(loc["id"])["gate"] == "soft" else [],
                "unreachable": False, "source_id": src_id,
                **_travel_metrics(src_id, traversed, travel_units)}
    if plan["reached"] is None:
        # The immediate next hop is hard-gated — the party doesn't move.
        await channel.send(_hard_block_message(plan["blocked"]))
        return

    reached_id = plan["reached"]
    reached = store.get_entity_by_id(reached_id) or loc
    log.info("_begin_travel: %s → reached id=%s name=%s (cost=%s km=%.2f hours=%.2f blocked=%s)",
             pc.name, reached_id, reached.get("name"), plan["cost"],
             plan.get("distance_km") or 0.0, plan.get("time_h") or 0.0, plan["blocked"])
    notice = _travel_notice(plan)
    if notice:
        await channel.send(notice)

    gs.clear_pending_freeplay_action()
    # Authored place → re-enter the scripted scene (keeps its challenges/encounter/entities);
    # emergent place → free location beat.
    scene_def = scenario.scene_by_id(reached_id)
    if scene_def:
        gs.goto_scene(scene_def)
    else:
        gs.goto_location(reached_id, title=reached["name"], summary=reached.get("notes") or "")
    if plan.get("time_h"):
        gs.advance_minutes(float(plan["time_h"]) * 60)
    elif plan["cost"]:
        gs.advance_time(plan["cost"])  # compatibility fallback for cost-only paths
    world_movement.advance_transits(gs.world_minutes())
    await _persist(gs)
    await _open_current_scene(channel, gs)
    if not (gs.combat and gs.combat.active):
        await _track_story(channel, gs)


async def _begin_scene_combat(channel, gs) -> None:
    combat_state = gs.start_scene_combat()
    if combat_state is None:
        await channel.send("這裡沒有可以戰鬥的對象。")
        return
    await channel.send(embed=embeds.combat_tracker_embed(gs))
    await _persist(gs)
    await _progress_combat(channel, gs)


async def _begin_entity_combat(channel, gs, trigger_entity_id: str | None = None) -> None:
    combat_state = gs.start_entity_combat(trigger_entity_id)
    if combat_state is None:
        await channel.send("這裡沒有可以戰鬥的對象。")
        return
    await channel.send(embed=embeds.combat_tracker_embed(gs))
    await _persist(gs)
    await _progress_combat(channel, gs)


async def _combat_declare(channel, user, gs, pc, intent, text: str) -> None:
    action = _match_action(pc, intent, text)
    if action is None:
        async def on_pick(interaction: discord.Interaction, label: str):
            action_name = _action_from_display(pc, label)
            await interaction.response.edit_message(
                content=f"➡️ {i18n.name(pc.name)}：**{i18n.action(action_name)}**",
                view=None,
            )
            await _resolve_combat_action(channel, user, gs, pc, action_name=action_name,
                                         target_ref=intent.target, allow_bonus=True)

        names = [i18n.action(a.name) for a in pc.actions if a.uses is None or a.uses > 0]
        await channel.send(content=f"{user.mention}，要使用哪個動作？", view=ChoiceView(user.id, names, on_pick))
        return

    from ..engine.types import ActionCost

    allow_bonus = action.cost is not ActionCost.BONUS
    await _resolve_combat_action(channel, user, gs, pc, action_name=action.name,
                                 target_ref=intent.target, allow_bonus=allow_bonus)


def _match_action(pc, intent, text: str):
    low = text.lower()
    for action in pc.actions:
        if action.name.lower() in low or i18n.action(action.name).lower() in low:
            return action
    if "heal" in low or "cure" in low or "治療" in text or "恢復" in text:
        return (
            pc.find_action("Heal")
            or pc.find_action("Healing Font")
            or pc.find_action("Catch Breath")
            or pc.find_action("Cure Wounds")
            or pc.find_action("Healing Word")
            or pc.find_action("Second Wind")
        )
    if intent.is_attack:
        from ..engine.types import ActionType

        for action in pc.actions:
            if action.type in (ActionType.MELEE_ATTACK, ActionType.RANGED_ATTACK, ActionType.SAVE_SPELL, ActionType.AUTO_HIT):
                return action
    return None


async def _resolve_combat_action(channel, user, gs, pc, action_name, target_ref,
                                 death_save: bool = False, allow_bonus: bool = False) -> None:
    if not death_save and action_name is not None:
        action = pc.find_action(action_name)
        if action is not None and target_ref is None:
            from ..engine.types import ActionType

            offensive = action.type in (ActionType.MELEE_ATTACK, ActionType.RANGED_ATTACK,
                                        ActionType.SAVE_SPELL, ActionType.AUTO_HIT)
            if offensive:
                enemies = combat.living_enemies(gs, pc)
                if len(enemies) > 1:
                    async def on_target(interaction: discord.Interaction, label: str):
                        target_name = _target_from_display(enemies, label)
                        await interaction.response.edit_message(
                            content=f"🎯 目標：**{i18n.name(target_name)}**",
                            view=None,
                        )
                        await _resolve_combat_action(channel, user, gs, pc, action_name, target_name,
                                                     allow_bonus=allow_bonus)

                    names = [i18n.name(e.name) for e in enemies]
                    await channel.send(content=f"{user.mention}，要攻擊哪個敵人？",
                                       view=ChoiceView(user.id, names, on_target))
                    return

    async def on_roll(interaction: discord.Interaction):
        try:
            if death_save:
                result = combat.take_death_save(gs, pc.id)
            else:
                result = combat.take_action(gs, pc.id, action_name, target_ref)
        except CombatError as exc:
            await interaction.response.edit_message(content=f"⚠️ {i18n.text(str(exc))}", embed=None, view=None)
            return
        await interaction.response.edit_message(content="🎲 擲骰中...", embed=None, view=None)
        await _send_dice_animation(channel, result.natural)
        prose = await _narrate_into_log(gs, result)
        await interaction.edit_original_response(content=None, embed=embeds.result_embed(result, prose))
        await _persist(gs)
        if allow_bonus and gs.combat and gs.combat.active:
            await _prompt_bonus_or_end(channel, user, gs, pc)
        else:
            combat.advance_turn(gs)
            await _progress_combat(channel, gs)

    label = "死亡豁免" if death_save else i18n.action(action_name)
    await channel.send(embed=embeds.roll_prompt_embed(pc, label, None), view=RollView(user.id, on_roll))


async def _prompt_bonus_or_end(channel, user, gs, pc) -> None:
    from ..engine.types import ActionCost

    if not (gs.combat and gs.combat.active):
        combat.advance_turn(gs)
        await _progress_combat(channel, gs)
        return
    bonus_actions = [a for a in pc.actions if a.cost is ActionCost.BONUS and (a.uses is None or a.uses > 0)]
    if gs.combat.bonus_used or not bonus_actions:
        combat.advance_turn(gs)
        await _progress_combat(channel, gs)
        return

    labels = [i18n.action(a.name) for a in bonus_actions] + ["結束回合"]

    async def on_choice(interaction: discord.Interaction, label: str):
        await interaction.response.edit_message(content=f"➡️ {i18n.name(pc.name)}：**{label}**", view=None)
        if label == "結束回合":
            combat.advance_turn(gs)
            await _progress_combat(channel, gs)
            return
        await _resolve_combat_action(channel, user, gs, pc, action_name=_action_from_display(pc, label), target_ref=None)

    await channel.send(content=f"{user.mention}，你還有一個**附贈動作**可以使用，也可以結束回合：",
                       view=ChoiceView(user.id, labels, on_choice))


async def _progress_combat(channel, gs) -> None:
    while gs.combat and gs.combat.active:
        actor = combat.current_combatant(gs)
        if actor is None:
            break
        if actor.is_pc and not actor.is_down:
            await channel.send(embed=embeds.combat_tracker_embed(gs))
            await channel.send(embed=embeds.turn_prompt_embed(actor, _mention_for(gs, actor.id)))
            return
        if actor.is_pc and actor.is_down and not actor.is_dead:
            mention = _mention_for(gs, actor.id)
            owner_id = int(mention.strip("<@>")) if mention else None
            if owner_id:
                async def on_roll(interaction: discord.Interaction, _aid=actor.id):
                    result = combat.take_death_save(gs, _aid)
                    await interaction.response.edit_message(content="🎲 擲骰中...", embed=None, view=None)
                    await _send_dice_animation(channel, result.natural)
                    prose = await _narrate_into_log(gs, result)
                    await interaction.edit_original_response(content=None, embed=embeds.result_embed(result, prose), view=None)
                    combat.advance_turn(gs)
                    await _persist(gs)
                    await _progress_combat(channel, gs)

                await channel.send(embed=embeds.turn_prompt_embed(actor, mention), view=RollView(owner_id, on_roll))
                return
            result = combat.take_death_save(gs, actor.id)
            await _send_dice_animation(channel, result.natural)
            prose = await _narrate_into_log(gs, result)
            await channel.send(embed=embeds.result_embed(result, prose))
            combat.advance_turn(gs)
            continue
        results = combat.run_monster_turn(gs)
        for result in results:
            await _send_dice_animation(channel, result.natural)
            prose = await _narrate_into_log(gs, result)
            await channel.send(embed=embeds.result_embed(result, prose))
        combat.advance_turn(gs)

    if gs.combat and not gs.combat.active:
        gs.reconcile_combat_entities()
        await channel.send(embed=embeds.combat_tracker_embed(gs))
        await _persist(gs)
        await _after_combat(channel, gs, gs.combat.outcome)


async def _after_combat(channel, gs, outcome: str) -> None:
    if outcome == "defeat":
        await _end_game(channel, gs, scenario.ENDINGS["defeat"])
        return
    # Winning the boss fight completes the climax beat.
    if gs.scene.id == "warren":
        gs.flags["climax_resolved"] = True
    # A-sandbox: GOALS is empty so _advance_story never reaches a terminal beat — winning a
    # fight just returns to free play. ENDINGS has no "victory" key now, hence .get().
    await _advance_story(channel, gs, win_ending=scenario.ENDINGS.get("victory", ""))


async def _advance_story(channel, gs, *, win_ending: str) -> None:
    """Goal director progression (replaces scenario.next_scene). Ends the one-shot when the
    terminal beat is met; else announces fresh progress, nudges toward the active beat, and
    hands the turn back — the party is never dragged into the next scripted scene."""
    progress = director.record(gs)
    if progress["all_done"]:
        await _end_game(channel, gs, win_ending)
        return
    for gid in progress["newly_done"]:
        title = next((g["title"] for g in scenario.GOALS if g["id"] == gid), gid)
        await channel.send(f"✅ 進展：**{title}**")
    active = progress["active"]
    if active and active.get("nudge"):
        await channel.send(f"🧭 {active['nudge']}")
    await _persist(gs)
    await _send_freeplay_turn_prompt(channel, gs)


async def _track_story(channel, gs) -> None:
    """After a non-combat beat (travel / exploration): fold the new world state into the
    goal director, announce any beat just completed, and nudge if the table has stalled."""
    progress = director.record(gs)
    for gid in progress["newly_done"]:
        title = next((g["title"] for g in scenario.GOALS if g["id"] == gid), gid)
        await channel.send(f"✅ 進展：**{title}**")
    if not progress["newly_done"]:
        director.note_beat(gs)
    hint = director.nudge_if_stalled(gs)
    if hint:
        await channel.send(f"🧭 {hint}")
    if await _maybe_npc_initiated_combat(channel, gs):
        return
    await _persist(gs)


def _npc_initiated_combat_line(attackers: list[dict], flipped: list[str]) -> str:
    names = "、".join(i18n.text(e["name"]) for e in attackers[:3])
    if len(attackers) > 3:
        names += "等人"
    if any(e["id"] in flipped for e in attackers):
        return f"⚔️ **{names}** 的敵意終於越線，殺意壓過最後一點克制，戰鬥爆發！"
    return f"⚔️ **{names}** 不再等待，主動發起攻擊！"


async def _maybe_npc_initiated_combat(channel, gs) -> bool:
    if gs.flags.get("over") or (gs.combat and gs.combat.active):
        return False
    # Provoked-but-left-alone units cool back to their baseline after a calm in-game day.
    recovered = gs.recover_provoked_dispositions()
    if recovered:
        log.info("provocation recovered to baseline: scope=%s ids=%s", gs.current_location_id, recovered)
    flipped = gs.escalate_hostiles()
    attackers = _attack_ready_entities(gs)
    if not attackers:
        return False
    await channel.send(_npc_initiated_combat_line(attackers, flipped))
    combat_state = gs.start_entity_combat()
    if combat_state is None:
        await _persist(gs)
        return False
    await channel.send(embed=embeds.combat_tracker_embed(gs))
    await _persist(gs)
    await _progress_combat(channel, gs)
    return True


async def _maybe_resolve_climax(channel, gs, result) -> None:
    if gs.scene.id != "warren" or gs.flags.get("over"):
        return
    social = {"diplomacy", "intimidation", "stealth", "deception"}
    summ = result.summary.lower()
    if result.success and any(s in summ for s in social):
        # A-sandbox: a scripted "peaceful" ending no longer exists. If one is configured we
        # honour it; otherwise the social win simply narrates and free play continues.
        peaceful = scenario.ENDINGS.get("peaceful")
        if peaceful:
            gs.flags["climax_resolved"] = True
            await _end_game(channel, gs, peaceful)
    elif result.success is False and any(s in summ for s in social):
        await channel.send("葛利克斯低吼一聲，談判破裂。哥布林們撲上前攻擊！")
        await _begin_scene_combat(channel, gs)


async def _open_current_scene(channel, gs) -> None:
    log.info("_open_current_scene: opening scene=%s channel=%s", gs.scene.id, getattr(channel, "id", None))
    async with channel.typing():
        prose = await orchestrator.open_scene(gs)
    try:
        embed = embeds.scene_status_embed(gs, prose, tips=gs.scene.onboarding)
        await channel.send(embed=embed)
        log.info("_open_current_scene: sent scene embed scene=%s", gs.scene.id)
    except discord.HTTPException as exc:
        log.exception("_open_current_scene: Discord rejected scene embed; sending plaintext fallback")
        await channel.send(f"📍 **{i18n.text(gs.scene.title)}**\n{i18n.text(prose)}")
    except Exception as exc:  # noqa: BLE001
        log.exception("_open_current_scene: failed to build/send scene embed; sending fallback")
        await channel.send(f"📍 **{i18n.text(gs.scene.title)}**\n{i18n.text(prose)}")
    await _persist(gs)
    scene_def = scenario.scene_by_id(gs.scene.id)
    if scene_def and scene_def.get("encounter") and gs.scene.id == "ambush":
        await _begin_scene_combat(channel, gs)
    else:
        await _send_freeplay_turn_prompt(channel, gs)


async def _end_game(channel, gs, ending: str) -> None:
    gs.flags["over"] = True
    campaigns.mark_finished(outcome=ending)
    gs.add_system_event("scene", "冒險告一段落。", ending)
    await channel.send(embed=discord.Embed(title="🏁 冒險結束", description=ending, color=discord.Color.gold()))
    await channel.send("感謝遊玩！可以使用 `/start` 再跑一次。")
    await _persist(gs)


@bot.tree.command(description="在此頻道開始新的冒險。")
async def start(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    msg = _start_block_message(interaction.channel_id)
    if msg:
        await interaction.response.send_message(msg, ephemeral=True)
        return

    previous = game_state.active_campaign()
    if previous and not game_state.has_discord_channel_binding(previous):
        log.warning(
            "/start: replacing active campaign without Discord channel binding (channel_id=%s)",
            previous.channel_id,
        )
    start_recording(channel_id=interaction.channel_id, actor=str(interaction.user.id))
    await interaction.response.defer(thinking=True)
    gs = game_state.reset_state(channel_id=interaction.channel_id)
    await location_registration.ensure_seed_location_cards(gs, scenario.LOCATIONS)
    await interaction.followup.send(embed=embeds.intro_embed())
    await interaction.followup.send(embed=embeds.roster_embed(gs), view=_join_view(interaction.channel))
    await _persist(gs)


def _join_view(channel) -> discord.ui.View:
    gs = game_state.get_state()
    view = discord.ui.View(timeout=600)
    for pc in gs.pcs():
        taken = pc.id in gs.players.values()
        label = f"{pc.portrait} 已選 {i18n.name(pc.name)}" if taken else f"{pc.portrait} 選擇 {i18n.name(pc.name)}"
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.success, disabled=taken)

        async def cb(interaction: discord.Interaction, _pid=pc.id, _name=pc.name):
            if not await _ensure_allowed_channel(interaction):
                return
            ok = gs.claim_pc(str(interaction.user.id), _pid, _display_name(interaction.user))
            if not ok:
                await interaction.response.send_message(f"{i18n.name(_name)} 已經被選走了，請選另一位英雄。", ephemeral=True)
                return
            await interaction.response.send_message(f"🎭 {interaction.user.mention} 現在是 **{i18n.name(_name)}**！")
            await interaction.message.edit(embed=embeds.roster_embed(gs), view=_join_view(channel))
            await _persist(gs)
            if len(gs.players) >= 2 and not gs.flags.get("opened"):
                gs.flags["opened"] = True
                await _open_current_scene(interaction.channel, gs)

        btn.callback = cb
        view.add_item(btn)
    return view


@bot.tree.command(description="選擇一名預製角色（bram 或 lyra）。")
@app_commands.describe(character="要扮演哪位英雄")
@app_commands.choices(character=[
    app_commands.Choice(name="Bram Ironwood", value="bram"),
    app_commands.Choice(name="Lyra Dawnbringer", value="lyra"),
])
async def join(interaction: discord.Interaction, character: str):
    if not await _ensure_allowed_channel(interaction):
        return
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請先使用 `/start`。", ephemeral=True)
        return
    target = gs.find_character(character)
    if target is None or not target.is_pc:
        names = " / ".join(i18n.name(p.name) for p in gs.pcs())
        await interaction.response.send_message(f"請選擇其中一位：{names}", ephemeral=True)
        return
    if gs.claim_pc(str(interaction.user.id), target.id, _display_name(interaction.user)):
        await interaction.response.send_message(f"🎭 你現在是 **{i18n.name(target.name)}**！")
        await _persist(gs)
        if len(gs.players) >= 2 and not gs.flags.get("opened"):
            gs.flags["opened"] = True
            await _open_current_scene(interaction.channel, gs)
    else:
        await interaction.response.send_message(f"**{i18n.name(target.name)}** 已經被選走了。", ephemeral=True)


@bot.tree.command(description="宣告你的角色現在要進行的動作。")
@app_commands.describe(text="例如：說服旅店老闆、搜索房間、攻擊哥布林")
async def action(interaction: discord.Interaction, text: str):
    if not await _ensure_allowed_channel(interaction):
        return
    log.info("/action received: user=%s channel=%s text=%r",
             interaction.user.id, interaction.channel_id, text)
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        log.warning("/action: no active campaign in channel=%s", interaction.channel_id)
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請先使用 `/start`。", ephemeral=True)
        return
    pc = gs.pc_for_user(str(interaction.user.id))
    if pc is None:
        log.warning("/action: user=%s has no PC (players=%s)", interaction.user.id, gs.players)
        await interaction.response.send_message("你還沒有角色。請先使用 `/join` 選擇角色。", ephemeral=True)
        return
    await interaction.response.send_message(f"🎬 {interaction.user.mention} 宣告：{text}")
    try:
        await process_action(interaction.channel, interaction.user, pc.id, text)
    except Exception:
        log.exception("/action: unhandled exception in process_action")
        raise


@bot.tree.command(description="顯示角色卡（預設顯示你的角色）。")
async def character(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請使用 `/start`。", ephemeral=True)
        return
    pc = gs.pc_for_user(str(interaction.user.id)) or (gs.pcs()[0] if gs.pcs() else None)
    if pc is None:
        await interaction.response.send_message("目前還沒有角色。", ephemeral=True)
        return
    await interaction.response.send_message(embed=embeds.character_embed(pc), ephemeral=True)


@bot.tree.command(description="重新描述目前的場景與處境（依當前狀態即時生成）。")
async def scene(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請使用 `/start`。", ephemeral=True)
        return
    # Generating the recap calls the model (can take a few seconds) — defer so the
    # interaction doesn't time out, then follow up with the live scene.
    await interaction.response.defer()
    prose = await orchestrator.recap_scene(gs)
    await interaction.followup.send(embed=embeds.scene_status_embed(gs, prose))


@bot.tree.command(description="手動擲骰，例如 /roll 1d20+3")
@app_commands.describe(notation="骰式，例如 1d20+3、2d6、d20")
async def roll(interaction: discord.Interaction, notation: str = "1d20"):
    if not await _ensure_allowed_channel(interaction):
        return
    from ..engine import dice

    try:
        r = dice.parse_and_roll(notation)
    except ValueError as exc:
        await interaction.response.send_message(f"⚠️ {i18n.text(str(exc))}", ephemeral=True)
        return
    file = None
    if r.notation.startswith("1d20") and len(r.rolls) == 1:
        file = dice_animations.animation_file(r.rolls[0])
    if file:
        await interaction.response.send_message(f"🎲 `{i18n.text(r.breakdown())}`", file=file)
    else:
        await interaction.response.send_message(f"🎲 `{i18n.text(r.breakdown())}`")


@bot.tree.command(description="開始目前場景的戰鬥（如果有）。")
async def fight(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請使用 `/start`。", ephemeral=True)
        return
    if gs.combat and gs.combat.active:
        await interaction.response.send_message("你們已經在戰鬥中了！", ephemeral=True)
        return
    await interaction.response.send_message("⚔️ 拔出武器！")
    await _begin_scene_combat(interaction.channel, gs)


@bot.tree.command(description="強制結束目前戰役。")
async def finish(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    gs, msg = _finish_target_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message(msg or "這裡目前沒有進行中的戰役。", ephemeral=True)
        return
    was_unbound = not game_state.has_discord_channel_binding(gs)
    gs.flags["over"] = True
    campaigns.mark_finished(outcome="forced")
    gs.clear_pending_freeplay_action()
    gs.combat = None
    gs.add_system_event("scene", "戰役被強制結束。", f"{interaction.user.display_name} 使用 /finish 結束了戰役。")
    await _persist(gs)
    if was_unbound:
        await interaction.response.send_message("🛑 已清除沒有 Discord 頻道綁定的舊戰役。現在可以再次使用 `/start`。")
    else:
        await interaction.response.send_message("🛑 已強制結束戰役。現在可以再次使用 `/start`。")
    finish_recording(reason=f"/finish by {interaction.user.id}")


@bot.tree.command(description="重新啟動機器人程序並重新讀取程式內容。")
async def restart(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    gs = game_state.get_state()
    if gs:
        await _persist(gs)
    await interaction.response.send_message("🔄 正在重新啟動機器人，稍後會重新連線。")
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable, "-m", "app.run"])


@bot.tree.command(description="查看玩法說明。")
async def help(interaction: discord.Interaction):
    if not await _ensure_allowed_channel(interaction):
        return
    e = discord.Embed(title="玩法說明", description=scenario.HOW_TO_PLAY, color=discord.Color.blurple())
    e.add_field(
        name="指令",
        value="/start ・ /join ・ /action ・ /character ・ /scene ・ /roll ・ /fight ・ /finish ・ /restart",
        inline=False,
    )
    await interaction.response.send_message(embed=e, ephemeral=True)


def run() -> None:
    bot.run(settings.discord_token)
