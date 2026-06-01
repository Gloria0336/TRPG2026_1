"""discord.Embed builders. Pure formatting — no game logic, no rolls."""
from __future__ import annotations

import discord

from ..content import scenario
from ..engine.types import Character, ResolutionResult, ResultKind
from ..state.game_state import GameState

GREEN = discord.Color.green()
RED = discord.Color.red()
BLURPLE = discord.Color.blurple()
GOLD = discord.Color.gold()


def intro_embed() -> discord.Embed:
    e = discord.Embed(title=f"🎲 {scenario.TITLE}", description=scenario.INTRO, color=GOLD)
    e.add_field(name="How to play", value=scenario.HOW_TO_PLAY, inline=False)
    e.set_footer(text="Use /join to pick a character, then just type what you do.")
    return e


def roster_embed(state: GameState) -> discord.Embed:
    e = discord.Embed(title="Choose your hero", color=BLURPLE,
                      description="Two pre-made characters — pick one with the buttons or `/join`.")
    for pc in state.pcs():
        taken = pc.id in state.players.values()
        who = " — *taken*" if taken else ""
        e.add_field(
            name=f"{pc.portrait} {pc.name} (Lv {pc.level}){who}",
            value=f"{pc.blurb}\nHP {pc.max_hp} · AC {pc.ac}",
            inline=False,
        )
    return e


def scene_embed(state: GameState, prose: str) -> discord.Embed:
    e = discord.Embed(title=state.scene.title, description=prose or state.scene.summary, color=GOLD)
    if state.scene.npcs:
        e.add_field(name="Present", value=", ".join(state.scene.npcs), inline=False)
    if state.scene.onboarding:
        tips = "\n".join(f"• {t}" for t in state.scene.onboarding)
        e.add_field(name="You could try", value=tips, inline=False)
    return e


def character_embed(c: Character) -> discord.Embed:
    color = GREEN if not c.is_down else RED
    e = discord.Embed(title=f"{c.portrait} {c.name}", description=c.blurb, color=color)
    abil = "  ".join(f"**{k}** {v:+d}".replace(f"{v:+d}", f"{v} ({c.mod(k):+d})") for k, v in c.abilities.items())
    e.add_field(name="Abilities", value=abil, inline=False)
    e.add_field(name="HP", value=f"{c.hp}/{c.max_hp}", inline=True)
    e.add_field(name="AC", value=str(c.ac), inline=True)
    e.add_field(name="Level", value=str(c.level), inline=True)
    if c.skill_prof:
        skills = ", ".join(f"{s.replace('_', ' ').title()} ({c.skill_bonus(s):+d})" for s in c.skill_prof)
        e.add_field(name="Trained skills", value=skills, inline=False)
    if c.actions:
        acts = "\n".join(_action_line(a) for a in c.actions)
        e.add_field(name="Actions", value=acts, inline=False)
    if c.conditions:
        e.add_field(name="Conditions", value=", ".join(c.conditions), inline=False)
    return e


def _action_line(a) -> str:
    uses = "" if a.uses is None else f" ({a.uses} left)"
    return f"**{a.name}**{uses} — {a.description}"


def roll_prompt_embed(actor: Character, label: str, dc: int | None) -> discord.Embed:
    desc = f"{actor.portrait} **{actor.name}** — {label}"
    if dc is not None:
        desc += f"\nTarget: **DC {dc}**"
    desc += "\n\nClick 🎲 to roll. *(The server rolls the real dice; the button just reveals it.)*"
    return discord.Embed(title="A roll is called for", description=desc, color=BLURPLE)


def result_embed(result: ResolutionResult, narration: str | None = None) -> discord.Embed:
    if result.success is True:
        color = GREEN
    elif result.success is False:
        color = RED
    else:
        color = BLURPLE
    e = discord.Embed(title=result.summary, color=color)
    if result.roll_breakdown:
        tag = ""
        if result.crit:
            tag = "  💥 **CRIT!**"
        elif result.fumble:
            tag = "  💀 **FUMBLE!**"
        e.add_field(name="🎲 Roll", value=f"`{result.roll_breakdown}`{tag}", inline=False)
    if result.deltas:
        e.add_field(name="What changed", value="\n".join(f"• {d}" for d in result.deltas), inline=False)
    if narration:
        e.add_field(name="📖 GM", value=narration, inline=False)
    return e


def combat_tracker_embed(state: GameState) -> discord.Embed:
    c = state.combat
    if not c:
        return discord.Embed(title="No active combat", color=BLURPLE)
    title = f"⚔️ Combat — Round {c.round}" + ("" if c.active else f" — {c.outcome}!")
    lines = []
    for cid, init in c.order:
        ch = state.characters.get(cid)
        if not ch:
            continue
        marker = "▶️ " if cid == c.current_id and c.active else "   "
        status = "💀" if ch.is_dead else ("🩸" if ch.is_down else f"{ch.hp}/{ch.max_hp}")
        lines.append(f"{marker}`{init:>2}` {ch.portrait} **{ch.name}** — {status}")
    e = discord.Embed(title=title, description="\n".join(lines), color=RED if c.active else GOLD)
    return e


def turn_prompt_embed(actor: Character, mention: str | None) -> discord.Embed:
    who = mention or f"**{actor.name}**"
    actions = ", ".join(a.name for a in actor.actions)
    if actor.is_down:
        return discord.Embed(
            title=f"{actor.portrait} {actor.name} is dying",
            description=f"{who}, it's your turn — roll a death save with the 🎲 button.",
            color=RED,
        )
    return discord.Embed(
        title=f"{actor.portrait} {actor.name}'s turn",
        description=f"{who}, what do you do?\nSay e.g. *“I attack a goblin with my {actor.actions[0].name}.”*\n"
                    f"Available: {actions}",
        color=BLURPLE,
    )
