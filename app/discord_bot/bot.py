"""Discord bot: natural-language play + slash commands + the dice-button flow.

One channel hosts one session (design MVP: single session). Players /join a pre-made PC,
then type what they do. The bot parses intent (AI), and for any roll it posts a 🎲 button
that asks the server to reveal an already-decided result (§9.3). Combat runs turn-by-turn.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..ai import orchestrator
from ..config import settings
from ..content import scenario
from ..engine import combat, resolution
from ..engine.combat import CombatError
from ..engine.types import IntentTier
from ..state import game_state
from . import embeds
from .views import ChoiceView, RollView

intents = discord.Intents.default()
intents.message_content = True  # privileged intent — enable it in the Developer Portal

bot = commands.Bot(command_prefix="!trpg ", intents=intents, help_command=None)


# ───────────────────────── lifecycle ─────────────────────────
@bot.event
async def on_ready():
    # Resume a saved session if one exists (no DB; JSON snapshot).
    if game_state.get_state() is None:
        saved = game_state.GameState.load()
        if saved:
            game_state.set_state(saved)
    try:
        if settings.discord_guild_id:
            guild = discord.Object(id=int(settings.discord_guild_id))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
    except Exception as exc:  # noqa: BLE001
        print(f"[bot] slash-command sync failed: {exc}")
    print(f"[bot] logged in as {bot.user} — ready.")


# ───────────────────────── helpers ─────────────────────────
def _state_for_channel(channel_id: int) -> game_state.GameState | None:
    gs = game_state.get_state()
    if gs and gs.started and gs.channel_id == channel_id and not gs.flags.get("over"):
        return gs
    return None


def _mention_for(gs: game_state.GameState, actor_id: str) -> str | None:
    for uid, pid in gs.players.items():
        if pid == actor_id:
            return f"<@{uid}>"
    return None


async def _persist(gs: game_state.GameState) -> None:
    try:
        gs.save()
    except OSError:
        pass


async def _narrate_into_log(gs: game_state.GameState, result) -> str:
    prose = await orchestrator.narrate(gs, result)
    if gs.event_log:
        gs.set_narration(gs.event_log[-1].id, prose)
    return prose


# ───────────────────────── natural-language entry ─────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.content:
        return
    # Let command framework handle prefixed commands; ignore everything outside a session.
    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return
    gs = _state_for_channel(message.channel.id)
    if gs is None:
        return
    pc = gs.pc_for_user(str(message.author.id))
    if pc is None:
        return  # not a joined player; stay quiet to avoid noise
    await process_action(message.channel, message.author, pc.id, message.content)


async def process_action(channel, user, actor_id: str, text: str) -> None:
    """Core handler: parse intent → route to check / combat / clarification."""
    gs = _state_for_channel(channel.id)
    if gs is None:
        return
    pc = gs.characters.get(actor_id)
    if pc is None:
        return

    in_combat = bool(gs.combat and gs.combat.active)
    if in_combat:
        cur = combat.current_combatant(gs)
        if cur is None or cur.id != pc.id:
            who = cur.name if cur else "no one"
            await channel.send(f"⏳ It's **{who}**'s turn, {user.mention} — hang tight.")
            return
        if pc.is_down:
            await _resolve_combat_action(channel, user, gs, pc, action_name=None, target_ref=None, death_save=True)
            return

    async with channel.typing():
        intent, proposed_dc = await orchestrator.interpret(gs, actor_id, text)

    if intent.tier is IntentTier.B and intent.candidates:
        async def on_choice(interaction: discord.Interaction, label: str):
            await interaction.response.edit_message(content=f"➡️ {pc.name}: **{label}**", view=None)
            await process_action(channel, user, actor_id, label)
        view = ChoiceView(user.id, intent.candidates, on_choice)
        await channel.send(
            content=f"🤔 {user.mention}, how do you want to do that?",
            view=view,
        )
        return

    if intent.tier is IntentTier.C:
        opts = intent.options or ["Look around", "Talk to someone", "Examine something"]
        async def on_choice_c(interaction: discord.Interaction, label: str):
            await interaction.response.edit_message(content=f"➡️ {pc.name}: **{label}**", view=None)
            await process_action(channel, user, actor_id, label)
        view = ChoiceView(user.id, opts, on_choice_c)
        await channel.send(content=f"❓ {intent.question or 'What do you mean?'} ({user.mention})", view=view)
        return

    # Tier A.
    if in_combat:
        await _combat_declare(channel, user, gs, pc, intent, text)
    elif intent.is_attack and scenario.scene_by_id(gs.scene.id) and scenario.scene_by_id(gs.scene.id).get("encounter"):
        await _begin_scene_combat(channel, gs)
    else:
        await _begin_check(channel, user, gs, pc, intent, proposed_dc)


# ───────────────────────── out-of-combat checks ─────────────────────────
async def _begin_check(channel, user, gs, pc, intent, proposed_dc) -> None:
    skill = resolution.normalize_approach(intent.approach or intent.action)
    dc = resolution.determine_dc(gs, intent, proposed_dc)
    label = f"{skill.replace('_', ' ').title()} check"
    if intent.target:
        label += f" — {intent.target}"

    async def on_roll(interaction: discord.Interaction):
        result = resolution.resolve(gs, intent, proposed_dc=proposed_dc)
        await interaction.response.edit_message(embed=embeds.result_embed(result), view=None)
        prose = await _narrate_into_log(gs, result)
        await interaction.edit_original_response(embed=embeds.result_embed(result, prose))
        await _persist(gs)
        await _maybe_resolve_climax(channel, gs, result)

    await channel.send(embed=embeds.roll_prompt_embed(pc, label, dc), view=RollView(user.id, on_roll))


# ───────────────────────── combat ─────────────────────────
async def _begin_scene_combat(channel, gs) -> None:
    combat_state = gs.start_scene_combat()
    if combat_state is None:
        await channel.send("There's nothing here to fight.")
        return
    await channel.send(embed=embeds.combat_tracker_embed(gs))
    await _persist(gs)
    await _progress_combat(channel, gs)


async def _combat_declare(channel, user, gs, pc, intent, text: str) -> None:
    """A PC declared an action on their turn. Match it to one of their Actions + a target."""
    action = _match_action(pc, intent, text)
    if action is None:
        # Couldn't tell which action — offer buttons.
        async def on_pick(interaction: discord.Interaction, label: str):
            await interaction.response.edit_message(content=f"➡️ {pc.name}: **{label}**", view=None)
            await _resolve_combat_action(channel, user, gs, pc, action_name=label,
                                         target_ref=intent.target, allow_bonus=True)
        names = [a.name for a in pc.actions if a.uses is None or a.uses > 0]
        await channel.send(content=f"{user.mention}, which action?", view=ChoiceView(user.id, names, on_pick))
        return
    # If the player's first move IS a bonus action, don't gate a follow-up on it.
    from ..engine.types import ActionCost
    allow_bonus = action.cost is not ActionCost.BONUS
    await _resolve_combat_action(channel, user, gs, pc, action_name=action.name,
                                 target_ref=intent.target, allow_bonus=allow_bonus)


def _match_action(pc, intent, text: str):
    # Try explicit action name mentioned in the text.
    for a in pc.actions:
        if a.name.lower() in text.lower():
            return a
    # Heuristics for common phrasings.
    low = text.lower()
    if "heal" in low or "cure" in low:
        return pc.find_action("Cure Wounds") or pc.find_action("Healing Word") or pc.find_action("Second Wind")
    if intent.is_attack:
        # default to the first attack-type action
        from ..engine.types import ActionType
        for a in pc.actions:
            if a.type in (ActionType.MELEE_ATTACK, ActionType.RANGED_ATTACK, ActionType.SAVE_SPELL, ActionType.AUTO_HIT):
                return a
    return None


async def _resolve_combat_action(channel, user, gs, pc, action_name, target_ref,
                                 death_save: bool = False, allow_bonus: bool = False) -> None:
    # Resolve target ambiguity up front for offensive actions.
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
                        await interaction.response.edit_message(content=f"🎯 Target: **{label}**", view=None)
                        await _resolve_combat_action(channel, user, gs, pc, action_name, label,
                                                     allow_bonus=allow_bonus)
                    names = [e.name for e in enemies]
                    await channel.send(content=f"{user.mention}, target which enemy?",
                                       view=ChoiceView(user.id, names, on_target))
                    return

    async def on_roll(interaction: discord.Interaction):
        try:
            if death_save:
                result = combat.take_death_save(gs, pc.id)
            else:
                result = combat.take_action(gs, pc.id, action_name, target_ref)
        except CombatError as exc:
            await interaction.response.edit_message(content=f"⚠️ {exc}", embed=None, view=None)
            return
        await interaction.response.edit_message(embed=embeds.result_embed(result), view=None)
        prose = await _narrate_into_log(gs, result)
        await interaction.edit_original_response(embed=embeds.result_embed(result, prose))
        await _persist(gs)
        # After the main action, a PC may still have a bonus action (§ 5e action economy).
        if allow_bonus and gs.combat and gs.combat.active:
            await _prompt_bonus_or_end(channel, user, gs, pc)
        else:
            combat.advance_turn(gs)
            await _progress_combat(channel, gs)

    label = "Death saving throw" if death_save else action_name
    await channel.send(embed=embeds.roll_prompt_embed(pc, label, None), view=RollView(user.id, on_roll))


async def _prompt_bonus_or_end(channel, user, gs, pc) -> None:
    """Offer any remaining bonus action, or let the player end their turn."""
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

    labels = [a.name for a in bonus_actions] + ["End turn"]

    async def on_choice(interaction: discord.Interaction, label: str):
        await interaction.response.edit_message(content=f"➡️ {pc.name}: **{label}**", view=None)
        if label == "End turn":
            combat.advance_turn(gs)
            await _progress_combat(channel, gs)
            return
        await _resolve_combat_action(channel, user, gs, pc, action_name=label, target_ref=None)

    await channel.send(content=f"{user.mention}, you still have a **bonus action** (or end your turn):",
                       view=ChoiceView(user.id, labels, on_choice))


async def _progress_combat(channel, gs) -> None:
    """Run automatic turns (monsters, downed PCs) until a living PC must act or combat ends."""
    while gs.combat and gs.combat.active:
        actor = combat.current_combatant(gs)
        if actor is None:
            break
        if actor.is_pc and not actor.is_down:
            await channel.send(embed=embeds.combat_tracker_embed(gs))
            await channel.send(embed=embeds.turn_prompt_embed(actor, _mention_for(gs, actor.id)))
            return  # wait for the player
        if actor.is_pc and actor.is_down and not actor.is_dead:
            # Auto-roll the death save with a 🎲 button owned by that player (if present).
            mention = _mention_for(gs, actor.id)
            owner_id = int(mention.strip("<@>")) if mention else None
            if owner_id:
                async def on_roll(interaction: discord.Interaction, _aid=actor.id):
                    result = combat.take_death_save(gs, _aid)
                    prose = await _narrate_into_log(gs, result)
                    await interaction.response.edit_message(embed=embeds.result_embed(result, prose), view=None)
                    combat.advance_turn(gs)
                    await _persist(gs)
                    await _progress_combat(channel, gs)
                await channel.send(embed=embeds.turn_prompt_embed(actor, mention),
                                   view=RollView(owner_id, on_roll))
                return
            # No human owner → resolve automatically.
            result = combat.take_death_save(gs, actor.id)
            prose = await _narrate_into_log(gs, result)
            await channel.send(embed=embeds.result_embed(result, prose))
            combat.advance_turn(gs)
            continue
        # Monster turn — auto.
        results = combat.run_monster_turn(gs)
        for r in results:
            prose = await _narrate_into_log(gs, r)
            await channel.send(embed=embeds.result_embed(r, prose))
        combat.advance_turn(gs)

    # Combat is over.
    if gs.combat and not gs.combat.active:
        await channel.send(embed=embeds.combat_tracker_embed(gs))
        await _persist(gs)
        await _after_combat(channel, gs, gs.combat.outcome)


# ───────────────────────── scenario flow ─────────────────────────
async def _after_combat(channel, gs, outcome: str) -> None:
    if outcome == "defeat":
        await _end_game(channel, gs, scenario.ENDINGS["defeat"])
        return
    # Victory. If this was the climax, win the adventure; otherwise advance a scene.
    if gs.scene.id == "warren":
        await _end_game(channel, gs, scenario.ENDINGS["victory"])
        return
    nxt = scenario.next_scene(gs.scene.id)
    if nxt is None:
        await _end_game(channel, gs, scenario.ENDINGS["victory"])
        return
    gs.goto_scene(nxt)
    await _open_current_scene(channel, gs)


async def _maybe_resolve_climax(channel, gs, result) -> None:
    """In the warren, a successful social/stealth check ends the adventure peacefully."""
    if gs.scene.id != "warren" or gs.flags.get("over"):
        return
    social = {"persuasion", "intimidation", "stealth", "deception"}
    summ = result.summary.lower()
    if result.success and any(s in summ for s in social):
        await _end_game(channel, gs, scenario.ENDINGS["peaceful"])
    elif result.success is False and any(s in summ for s in social):
        await channel.send("Grix snarls — talk has failed. The goblins lunge to attack!")
        await _begin_scene_combat(channel, gs)


async def _open_current_scene(channel, gs) -> None:
    async with channel.typing():
        prose = await orchestrator.open_scene(gs)
    await channel.send(embed=embeds.scene_embed(gs, prose))
    await _persist(gs)
    # Combat scenes start immediately (the ambush); choice scenes wait for the players.
    scene_def = scenario.scene_by_id(gs.scene.id)
    if scene_def and scene_def.get("encounter") and gs.scene.id == "ambush":
        await _begin_scene_combat(channel, gs)


async def _end_game(channel, gs, ending: str) -> None:
    gs.flags["over"] = True
    gs.add_system_event("scene", "The adventure concludes.", ending)
    await channel.send(embed=discord.Embed(title="🏁 The adventure ends", description=ending,
                                           color=discord.Color.gold()))
    await channel.send("Thanks for playing! Use `/start` to run it again.")
    await _persist(gs)


# ───────────────────────── slash commands ─────────────────────────
@bot.tree.command(description="Start a new adventure in this channel.")
async def start(interaction: discord.Interaction):
    gs = game_state.reset_state(channel_id=interaction.channel_id)
    await interaction.response.send_message(embed=embeds.intro_embed())
    await interaction.followup.send(embed=embeds.roster_embed(gs), view=_join_view(interaction.channel))
    await _persist(gs)


def _join_view(channel) -> discord.ui.View:
    gs = game_state.get_state()
    view = discord.ui.View(timeout=600)
    for pc in gs.pcs():
        btn = discord.ui.Button(label=f"Play {pc.name}", emoji=pc.portrait, style=discord.ButtonStyle.success)

        async def cb(interaction: discord.Interaction, _pid=pc.id, _name=pc.name):
            ok = gs.claim_pc(str(interaction.user.id), _pid)
            if not ok:
                await interaction.response.send_message(f"{_name} is already taken — pick the other hero.",
                                                         ephemeral=True)
                return
            await interaction.response.send_message(f"🎭 {interaction.user.mention} is now **{_name}**!")
            await _persist(gs)
            # Once both heroes are claimed, open the first scene.
            if len(gs.players) >= 2 and not gs.flags.get("opened"):
                gs.flags["opened"] = True
                await _open_current_scene(interaction.channel, gs)
        btn.callback = cb
        view.add_item(btn)
    return view


@bot.tree.command(description="Claim a pre-made character (bram or lyra).")
@app_commands.describe(character="Which hero to play")
async def join(interaction: discord.Interaction, character: str):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("No active game here. Use `/start` first.", ephemeral=True)
        return
    target = gs.find_character(character)
    if target is None or not target.is_pc:
        names = " / ".join(p.name for p in gs.pcs())
        await interaction.response.send_message(f"Pick one of: {names}", ephemeral=True)
        return
    if gs.claim_pc(str(interaction.user.id), target.id):
        await interaction.response.send_message(f"🎭 You are now **{target.name}**!")
        await _persist(gs)
        if len(gs.players) >= 2 and not gs.flags.get("opened"):
            gs.flags["opened"] = True
            await _open_current_scene(interaction.channel, gs)
    else:
        await interaction.response.send_message(f"**{target.name}** is already taken.", ephemeral=True)


@bot.tree.command(description="Show a character sheet (yours by default).")
async def character(interaction: discord.Interaction):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("No active game here. Use `/start`.", ephemeral=True)
        return
    pc = gs.pc_for_user(str(interaction.user.id)) or (gs.pcs()[0] if gs.pcs() else None)
    if pc is None:
        await interaction.response.send_message("No characters yet.", ephemeral=True)
        return
    await interaction.response.send_message(embed=embeds.character_embed(pc), ephemeral=True)


@bot.tree.command(description="Show the current scene.")
async def scene(interaction: discord.Interaction):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("No active game here. Use `/start`.", ephemeral=True)
        return
    await interaction.response.send_message(embed=embeds.scene_embed(gs, gs.scene.summary))


@bot.tree.command(description="Roll dice manually, e.g. /roll 1d20+3")
@app_commands.describe(notation="Dice like 1d20+3, 2d6, d20")
async def roll(interaction: discord.Interaction, notation: str = "1d20"):
    from ..engine import dice
    try:
        r = dice.parse_and_roll(notation)
    except ValueError as exc:
        await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        return
    await interaction.response.send_message(f"🎲 `{r.breakdown()}`")


@bot.tree.command(description="Advance to the next scene (skip ahead).")
async def next(interaction: discord.Interaction):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("No active game here. Use `/start`.", ephemeral=True)
        return
    if gs.combat and gs.combat.active:
        await interaction.response.send_message("Finish the current fight first!", ephemeral=True)
        return
    nxt = scenario.next_scene(gs.scene.id)
    if nxt is None:
        await interaction.response.send_message("This is the final scene.", ephemeral=True)
        return
    await interaction.response.send_message(f"⏭️ Moving on to **{nxt['title']}**…")
    gs.goto_scene(nxt)
    await _open_current_scene(interaction.channel, gs)


@bot.tree.command(description="Start the current scene's fight (if any).")
async def fight(interaction: discord.Interaction):
    gs = _state_for_channel(interaction.channel_id)
    if gs is None:
        await interaction.response.send_message("No active game here. Use `/start`.", ephemeral=True)
        return
    if gs.combat and gs.combat.active:
        await interaction.response.send_message("You're already in combat!", ephemeral=True)
        return
    await interaction.response.send_message("⚔️ Weapons out!")
    await _begin_scene_combat(interaction.channel, gs)


@bot.tree.command(description="How to play.")
async def help(interaction: discord.Interaction):
    e = discord.Embed(title="How to play", description=scenario.HOW_TO_PLAY, color=discord.Color.blurple())
    e.add_field(name="Commands",
                value="/start · /join · /character · /scene · /roll · /next · /fight", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


def run() -> None:
    bot.run(settings.discord_token)
