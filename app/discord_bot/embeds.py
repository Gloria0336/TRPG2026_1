"""discord.Embed builders. Pure formatting, no game logic or rolls."""
from __future__ import annotations

import discord

from ..content import scenario
from ..engine.types import Character, ResolutionResult, ResultBand
from ..state.game_state import GameState
from . import i18n

GREEN = discord.Color.green()
RED = discord.Color.red()
BLURPLE = discord.Color.blurple()
GOLD = discord.Color.gold()
# Four-degree result colors (§4.4): CRIT_SUCCESS gold, SUCCESS green, FAILURE red,
# CRIT_FAILURE dark red.
DARK_RED = discord.Color.dark_red()


def intro_embed() -> discord.Embed:
    e = discord.Embed(title=f"🎲 {scenario.TITLE}", description=scenario.INTRO, color=GOLD)
    e.add_field(name="玩法說明", value=scenario.HOW_TO_PLAY, inline=False)
    e.set_footer(text="使用 /join 選擇角色，接著用 /action 宣告你想做的事。")
    return e


def roster_embed(state: GameState) -> discord.Embed:
    e = discord.Embed(
        title="角色介紹與選擇",
        color=BLURPLE,
        description="冒險需要兩位英雄同行。請閱讀下方介紹，然後點選按鈕或使用 `/join` 選擇一位。",
    )
    for pc in state.pcs():
        taken = pc.id in state.players.values()
        who = "（已被選走）" if taken else ""
        skills = ", ".join(f"{i18n.skill(s)}({rank})" for s, rank in pc.skill_prof.items()) or "無"
        actions = ", ".join(i18n.action(a.name) for a in pc.actions[:3])
        e.add_field(
            name=f"{i18n.character_label(pc)}（等級 {pc.level}）{who}",
            value=(
                f"{pc.blurb}\n"
                f"生命值 {pc.max_hp} ・ 護甲 {pc.ac}\n"
                f"公會階級 {pc.guild_rank} ・ SP {pc.skill_points} ・ merit {pc.merit}\n"
                f"熟練技能：{skills}\n"
                f"主要動作：{actions}"
            ),
            inline=False,
        )
    e.set_footer(text="選好後會綁定你的 Discord 使用者；兩位玩家都選完後，冒險會自動開始。")
    return e




def scene_status_embed(state: GameState, prose: str, *, tips: list[str] | None = None) -> discord.Embed:
    """Live scene view (used by /scene AND the location opener). Reads the location label and
    the entities actually present right now (not the static authored scene list), so a
    departed NPC doesn't linger and the header reflects where the party currently is.
    `tips` optionally appends the onboarding hints shown when first entering a location."""
    from ..db import store

    title = state.scene.title
    try:
        loc = store.get_entity_by_id(state.current_location_id)
        if loc and loc.get("name"):
            title = loc["name"]
    except Exception:  # noqa: BLE001 — formatting must never break the command
        pass
    e = discord.Embed(title=i18n.text(title), description=i18n.text(prose), color=GOLD)
    present = [p for p in state.present_entities() if p.get("kind") != "location"]
    if present:
        e.add_field(name="在場", value="、".join(i18n.text(p["name"]) for p in present), inline=False)
    if tips:
        e.add_field(name="你可以嘗試", value="\n".join(f"• {i18n.text(t)}" for t in tips), inline=False)
    e.set_footer(text=f"時段：{state.time_of_day()}")
    return e


def character_embed(c: Character) -> discord.Embed:
    color = GREEN if not c.is_down else RED
    e = discord.Embed(title=i18n.character_label(c), description=i18n.text(c.blurb), color=color)
    abil = "  ".join(f"**{i18n.ABILITY_ZH.get(k, k)}** {v} ({c.mod(k):+d})" for k, v in c.abilities.items())
    e.add_field(name="能力值", value=abil, inline=False)
    e.add_field(name="生命值", value=f"{c.hp}/{c.max_hp}", inline=True)
    e.add_field(name="護甲", value=str(c.ac), inline=True)
    e.add_field(name="等級", value=str(c.level), inline=True)
    e.add_field(name="公會", value=f"{c.guild_rank} 級｜merit {c.merit}｜聲望 {c.standing}", inline=False)
    e.add_field(name="技能點", value=str(c.skill_points), inline=True)
    if c.skill_prof:
        skills = ", ".join(
            f"{i18n.skill(s)} {rank} ({c.skill_bonus(s):+d})"
            for s, rank in c.skill_prof.items()
        )
        e.add_field(name="熟練技能", value=skills, inline=False)
    if c.lore_prof:
        lore = ", ".join(f"{name} {rank} ({c.lore_bonus(name):+d})" for name, rank in c.lore_prof.items())
        e.add_field(name="Lore", value=lore, inline=False)
    if c.actions:
        acts = "\n".join(_action_line(a) for a in c.actions)
        e.add_field(name="動作", value=acts, inline=False)
    if c.conditions:
        e.add_field(name="狀態", value=", ".join(i18n.condition(x) for x in c.conditions), inline=False)
    try:
        from ..db import store

        inventory = store.get_inventory(c.id)
    except Exception:  # noqa: BLE001
        inventory = []
    if inventory:
        labels = {
            "weapon": "武器",
            "armor": "護甲",
            "shield": "盾牌",
            "consumable": "消耗品",
            "gear": "裝備",
            "treasure": "財物",
            "key_item": "關鍵物品",
            "misc": "雜物",
        }
        grouped: dict[str, list[str]] = {}
        for item in inventory:
            qty = int(item.get("quantity") or 1)
            suffix = f" x{qty}" if qty > 1 else ""
            equipped = "（已裝備）" if item.get("equipped") else ""
            grouped.setdefault(item.get("category") or "misc", []).append(
                f"{item.get('name')}{suffix}{equipped}"
            )
        lines = []
        for category, names in grouped.items():
            lines.append(f"**{labels.get(category, category)}**：{', '.join(names)}")
        e.add_field(name="背包", value="\n".join(lines)[:1024], inline=False)
    return e


def _action_line(a) -> str:
    uses = "" if a.uses is None else f"（剩餘 {a.uses} 次）"
    return f"**{i18n.action(a.name)}**{uses}：{i18n.text(a.description)}"


def roll_prompt_embed(
    actor: Character, label: str, dc: int | None, dc_note: str | None = None,
) -> discord.Embed:
    desc = f"{i18n.character_label(actor)}：{i18n.text(label)}"
    if dc is not None:
        desc += f"\n目標：**DC {dc}**"
        if dc_note:
            desc += f"（{dc_note}）"
    desc += "\n\n點擊 🎲 擲骰。（伺服器會擲出真正結果；按鈕只負責公開結果。）"
    return discord.Embed(title="需要擲骰", description=desc, color=BLURPLE)


def result_embed(result: ResolutionResult, narration: str | None = None) -> discord.Embed:
    if result.band is ResultBand.CRIT_SUCCESS:
        color = GOLD
    elif result.band is ResultBand.CRIT_FAILURE:
        color = DARK_RED
    elif result.success is True:
        color = GREEN
    elif result.success is False:
        color = RED
    else:
        color = BLURPLE
    e = discord.Embed(title=i18n.text(result.summary), color=color)
    if result.roll_breakdown:
        # Degree tag for checks comes from the band; attacks (band is None) fall back to
        # the nat-20/nat-1 crit/fumble flags.
        tag = ""
        if result.band is ResultBand.CRIT_SUCCESS:
            tag = "  ⭐ **大成功！**"
        elif result.band is ResultBand.CRIT_FAILURE:
            tag = "  ⚠️ **大失敗！**"
        elif result.crit:
            tag = "  ⭐ **重擊！**"
        elif result.fumble:
            tag = "  ⚠️ **大失敗！**"
        e.add_field(name="🎲 擲骰", value=f"`{i18n.text(result.roll_breakdown)}`{tag}", inline=False)
    if result.deltas:
        e.add_field(name="狀態變化", value="\n".join(f"• {i18n.text(d)}" for d in result.deltas), inline=False)
    if narration:
        e.add_field(name="GM", value=i18n.text(narration), inline=False)
    return e


def combat_tracker_embed(state: GameState) -> discord.Embed:
    c = state.combat
    if not c:
        return discord.Embed(title="目前沒有戰鬥", color=BLURPLE)
    title = f"⚔️ 戰鬥：第 {c.round} 回合" + ("" if c.active else f"：{i18n.outcome(c.outcome)}！")
    lines = []
    for cid, init in c.order:
        ch = state.characters.get(cid)
        if not ch:
            continue
        marker = "👉 " if cid == c.current_id and c.active else "   "
        status = "死亡" if ch.is_dead else ("倒下" if ch.is_down else f"{ch.hp}/{ch.max_hp}")
        lines.append(f"{marker}`{init:>2}` {i18n.character_label(ch)}：{status}")
    return discord.Embed(title=title, description="\n".join(lines), color=RED if c.active else GOLD)


def turn_prompt_embed(actor: Character, mention: str | None) -> discord.Embed:
    who = mention or f"**{i18n.name(actor.name)}**"
    actions = ", ".join(i18n.action(a.name) for a in actor.actions)
    if actor.is_down:
        return discord.Embed(
            title=f"{i18n.character_label(actor)} 正在瀕死",
            description=f"{who}，輪到你了。請用 🎲 按鈕進行死亡豁免。",
            color=RED,
        )
    return discord.Embed(
        title=f"{i18n.character_label(actor)} 的回合",
        description=f"{who}，你要做什麼？\n例如：攻擊一隻哥布林，或使用 {i18n.action(actor.actions[0].name)}。\n"
                    f"可用動作：{actions}",
        color=BLURPLE,
    )
