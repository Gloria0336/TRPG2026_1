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
from ..content import director, scenario
from ..db import store
from ..engine import combat, resolution
from ..engine.combat import CombatError
from ..engine.types import IntentTier
from ..logging_setup import finish_recording, get_logger, start_recording
from ..state import game_state
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
    return False

intents = discord.Intents.default()
intents.message_content = False

bot = commands.Bot(command_prefix="!trpg ", intents=intents, help_command=None)


@bot.event
async def on_ready():
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


async def _narrate_into_log(gs: game_state.GameState, result) -> str:
    prose = await orchestrator.narrate(gs, result)
    if gs.event_log:
        gs.set_narration(gs.event_log[-1].id, prose)
    await _apply_entity_updates(gs, prose, result)
    return prose


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
                    ent_id = store.promote_mention(scope, name, kind)
                    log.info("entity promoted after %d mention(s): scope=%s name=%s kind=%s id=%s",
                             count, scope, name, kind, ent_id)
                else:
                    log.info("entity mention tallied (%d/%d): scope=%s name=%s kind=%s",
                             count, threshold, scope, name, kind)
                continue
            ent_id = store.apply_delta(scope, d)
            if ent_id:
                log.info("entity delta applied: scope=%s entity=%s %s",
                         scope, ent_id, delta.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001 — continuity layer must not break play
        log.warning("entity update failed (%s): %s", type(exc).__name__, exc)
    try:
        store.set_current_summary(scope, prompts.compose_scene_summary(gs))
    except Exception as exc:  # noqa: BLE001
        log.warning("scene summary refresh failed (%s): %s", type(exc).__name__, exc)
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


async def _send_freeplay_turn_prompt(channel, gs: game_state.GameState) -> None:
    if gs.flags.get("over") or (gs.combat and gs.combat.active):
        return
    actor_id = gs.current_freeplay_actor_id()
    if actor_id:
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
        # continuation (skip the "complete your previous option" block) and
        # attach the text to the last GM question so the parser sees it.
        clarif_open = gs.clarification_turn_count(pc.id) > 0
        if clarif_open:
            continue_pending = True
            gs.record_clarification_reply(pc.id, text)
        if not await _ensure_freeplay_turn(channel, gs, pc, user, continue_pending):
            log.info("process_action: freeplay turn check rejected for %s", pc.name)
            return

    clarification = gs.get_clarification(actor_id)
    async with channel.typing():
        intent, proposed_dc = await orchestrator.interpret(
            gs, actor_id, text, clarification=clarification,
        )

    log.info("process_action: dispatch intent.tier=%s action=%s target=%s approach=%s is_attack=%s implausible=%s proposed_dc=%s",
             intent.tier.value, intent.action, intent.target, intent.approach, intent.is_attack, intent.implausible, proposed_dc)

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

        gs.push_clarification(pc.id, question)
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
    elif intent.needs_check:
        log.info("process_action → _begin_check (out-of-combat check)")
        await _begin_check(channel, user, gs, pc, intent, proposed_dc)
    else:
        log.info("process_action → _begin_narrative (trivial no-roll beat)")
        await _begin_narrative(channel, user, gs, pc, intent)


async def _begin_check(channel, user, gs, pc, intent, proposed_dc) -> None:
    skill = resolution.normalize_approach(intent.approach or intent.action)
    dc = resolution.determine_dc(gs, intent, proposed_dc)
    log.info("_begin_check: pc=%s skill=%s dc=%s target=%s proposed_dc=%s",
             pc.name, skill, dc, intent.target, proposed_dc)
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
        if other_pc.skill_prof.get(skill) not in ("prof", "expertise"):
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
        result = resolution.resolve(gs, intent, proposed_dc=proposed_dc, helpers=list(view.helpers))
        await interaction.response.edit_message(content="🎲 擲骰中...", embed=None, view=None)
        await _send_dice_animation(channel, result.natural)
        prose = await _narrate_into_log(gs, result)
        await interaction.edit_original_response(content=None, embed=embeds.result_embed(result, prose))
        gs.complete_freeplay_action(pc.id)
        await _persist(gs)
        await _maybe_resolve_climax(channel, gs, result)
        await _send_freeplay_turn_prompt(channel, gs)

    view.on_roll = on_roll

    gs.begin_freeplay_action(pc.id)
    await _persist(gs)
    await channel.send(embed=embeds.roll_prompt_embed(pc, label, dc), view=view)


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


def _resolve_travel_target(gs, target: str) -> dict | None:
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
            # "leave here" — pick the nearest authored exit, else create a generic outside.
            for other in store.get_locations():
                if other["id"] != here["id"]:
                    return other
            return store.resolve_or_register_location(f"{here['name']}外")
    return store.resolve_or_register_location(target)


async def _begin_travel(channel, user, gs, pc, intent) -> None:
    """Free travel (design: location is first-class state). Resolve/register the
    destination as a location entity, move the party there, then open it — so the
    structured location follows the fiction instead of being left behind."""
    loc = None
    try:
        loc = _resolve_travel_target(gs, intent.target or "")
    except Exception as exc:  # noqa: BLE001 — continuity layer must not break play
        log.warning("_begin_travel: resolve location failed (%s): %s", type(exc).__name__, exc)
    if loc is None:
        # Couldn't pin a destination — fall back to a plain no-roll beat.
        await _begin_narrative(channel, user, gs, pc, intent)
        return
    if loc["id"] == gs.current_location_id:
        # Resolver returned 'here' — the special-case logic above should have
        # redirected, but in case of an alias collision we'd otherwise loop. Treat
        # as a narrative non-move so play still advances.
        log.info("_begin_travel: target resolved to current location %s — narrating in-place", loc["id"])
        await _begin_narrative(channel, user, gs, pc, intent)
        return
    log.info("_begin_travel: %s → location id=%s name=%s", pc.name, loc["id"], loc["name"])
    gs.clear_pending_freeplay_action()
    # Authored place → re-enter the scripted scene (keeps its challenges/encounter/entities);
    # emergent place → free location beat.
    scene_def = scenario.scene_by_id(loc["id"])
    if scene_def:
        gs.goto_scene(scene_def)
    else:
        gs.goto_location(loc["id"], title=loc["name"], summary=loc.get("notes") or "")
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
        return pc.find_action("Cure Wounds") or pc.find_action("Healing Word") or pc.find_action("Second Wind")
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
    # Goal-driven, not the linear next_scene rail: end on the terminal beat, otherwise
    # return to free play and let the players choose where to go (with a nudge).
    await _advance_story(channel, gs, win_ending=scenario.ENDINGS["victory"])


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
    await _persist(gs)


async def _maybe_resolve_climax(channel, gs, result) -> None:
    if gs.scene.id != "warren" or gs.flags.get("over"):
        return
    social = {"persuasion", "intimidation", "stealth", "deception"}
    summ = result.summary.lower()
    if result.success and any(s in summ for s in social):
        gs.flags["climax_resolved"] = True
        await _end_game(channel, gs, scenario.ENDINGS["peaceful"])
    elif result.success is False and any(s in summ for s in social):
        await channel.send("葛利克斯低吼一聲，談判破裂。哥布林們撲上前攻擊！")
        await _begin_scene_combat(channel, gs)


async def _open_current_scene(channel, gs) -> None:
    async with channel.typing():
        prose = await orchestrator.open_scene(gs)
    await channel.send(embed=embeds.scene_embed(gs, prose))
    await _persist(gs)
    scene_def = scenario.scene_by_id(gs.scene.id)
    if scene_def and scene_def.get("encounter") and gs.scene.id == "ambush":
        await _begin_scene_combat(channel, gs)
    else:
        await _send_freeplay_turn_prompt(channel, gs)


async def _end_game(channel, gs, ending: str) -> None:
    gs.flags["over"] = True
    gs.add_system_event("scene", "冒險告一段落。", ending)
    await channel.send(embed=discord.Embed(title="🏁 冒險結束", description=ending, color=discord.Color.gold()))
    await channel.send("感謝遊玩！可以使用 `/start` 再跑一次。")
    await _persist(gs)


@bot.tree.command(description="在此頻道開始新的冒險。")
async def start(interaction: discord.Interaction):
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
    gs = game_state.reset_state(channel_id=interaction.channel_id)
    await interaction.response.send_message(embed=embeds.intro_embed())
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
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請使用 `/start`。", ephemeral=True)
        return
    pc = gs.pc_for_user(str(interaction.user.id)) or (gs.pcs()[0] if gs.pcs() else None)
    if pc is None:
        await interaction.response.send_message("目前還沒有角色。", ephemeral=True)
        return
    await interaction.response.send_message(embed=embeds.character_embed(pc), ephemeral=True)


@bot.tree.command(description="顯示目前場景。")
async def scene(interaction: discord.Interaction):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請使用 `/start`。", ephemeral=True)
        return
    await interaction.response.send_message(embed=embeds.scene_embed(gs, gs.scene.summary))


@bot.tree.command(description="手動擲骰，例如 /roll 1d20+3")
@app_commands.describe(notation="骰式，例如 1d20+3、2d6、d20")
async def roll(interaction: discord.Interaction, notation: str = "1d20"):
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


@bot.tree.command(description="前往下一個場景（跳過目前段落）。")
async def next(interaction: discord.Interaction):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("這裡目前沒有進行中的遊戲。請使用 `/start`。", ephemeral=True)
        return
    if gs.combat and gs.combat.active:
        await interaction.response.send_message("請先結束目前的戰鬥！", ephemeral=True)
        return
    nxt = scenario.next_scene(gs.scene.id)
    if nxt is None:
        await interaction.response.send_message("這已經是最後一個場景了。", ephemeral=True)
        return
    await interaction.response.send_message(f"➡️ 前往 **{nxt['title']}**。")
    gs.goto_scene(nxt)
    await _open_current_scene(interaction.channel, gs)


@bot.tree.command(description="開始目前場景的戰鬥（如果有）。")
async def fight(interaction: discord.Interaction):
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
    gs, msg = _finish_target_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message(msg or "這裡目前沒有進行中的戰役。", ephemeral=True)
        return
    was_unbound = not game_state.has_discord_channel_binding(gs)
    gs.flags["over"] = True
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
    gs = game_state.get_state()
    if gs:
        await _persist(gs)
    await interaction.response.send_message("🔄 正在重新啟動機器人，稍後會重新連線。")
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable, "-m", "app.run"])


@bot.tree.command(description="查看玩法說明。")
async def help(interaction: discord.Interaction):
    e = discord.Embed(title="玩法說明", description=scenario.HOW_TO_PLAY, color=discord.Color.blurple())
    e.add_field(
        name="指令",
        value="/start ・ /join ・ /action ・ /character ・ /scene ・ /roll ・ /next ・ /fight ・ /finish ・ /restart",
        inline=False,
    )
    await interaction.response.send_message(embed=e, ephemeral=True)


def run() -> None:
    bot.run(settings.discord_token)
