"""Interactive components: the server-authoritative dice button (§9.3) and the A/B/C
method-choice buttons (§8.3 / §9.1).

The dice button is a one-shot trigger: it never rolls on the client, it just asks the
server to reveal an already-decided result, and it disables itself after one use so a
player can't re-roll.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

from ..config import settings

OnRoll = Callable[[discord.Interaction], Awaitable[None]]
OnChoice = Callable[[discord.Interaction, str], Awaitable[None]]


def _is_allowed_channel_id(channel_id: int | None) -> bool:
    allowed = settings.parsed_discord_allowed_channel_ids
    return not allowed or (channel_id in allowed)


async def _reject_disallowed_channel(interaction: discord.Interaction) -> None:
    allowed = sorted(settings.parsed_discord_allowed_channel_ids)
    channel_list = "、".join(f"<#{channel_id}>" for channel_id in allowed)
    msg = f"這個 bot 只能在指定頻道使用：{channel_list}" if channel_list else "這個 bot 目前沒有允許使用的 Discord 頻道。"
    await interaction.response.send_message(msg, ephemeral=True)


class RollView(discord.ui.View):
    """A single 🎲 button restricted to one player, usable exactly once.

    Optionally exposes a 🤝 助攻 button for a designated helper (§4.9). The actor's
    on_roll closure can read `view.helpers` (list of PC ids) to feed resolution.resolve.
    The helper button is a one-shot toggle: click once to opt in, click again to opt out;
    it disables itself the moment the actor rolls.
    """

    def __init__(
        self,
        allowed_user_id: int,
        on_roll: OnRoll | None,
        *,
        helper_user_id: int | None = None,
        helper_pc_id: str | None = None,
        helper_label: str = "我來協助 (+2)",
        timeout: float | None = 300,
    ):
        super().__init__(timeout=timeout)
        self.allowed_user_id = allowed_user_id
        self.on_roll = on_roll
        self.used = False
        # Helper opt-in state. `helpers` is read by the on_roll closure right before
        # the engine call so the actor sees whatever state the partner left it in.
        self.helpers: list[str] = []
        self.helper_user_id = helper_user_id
        self.helper_pc_id = helper_pc_id

        self._roll_btn = discord.ui.Button(label="擲骰", emoji="🎲", style=discord.ButtonStyle.primary)
        self._roll_btn.callback = self._on_roll_click
        self.add_item(self._roll_btn)

        if helper_user_id is not None and helper_pc_id is not None:
            self._assist_btn = discord.ui.Button(
                label=helper_label,
                emoji="🤝",
                style=discord.ButtonStyle.secondary,
            )
            self._assist_btn.callback = self._on_assist_click
            self.add_item(self._assist_btn)
        else:
            self._assist_btn = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _is_allowed_channel_id(interaction.channel_id):
            return True
        await _reject_disallowed_channel(interaction)
        return False

    async def _on_roll_click(self, interaction: discord.Interaction):
        if interaction.user.id != self.allowed_user_id:
            await interaction.response.send_message("這不是你的擲骰。", ephemeral=True)
            return
        if self.used:
            await interaction.response.send_message("這次擲骰已經完成了。", ephemeral=True)
            return
        self.used = True
        self._roll_btn.disabled = True
        if self._assist_btn is not None:
            self._assist_btn.disabled = True
        self.stop()
        await self.on_roll(interaction)

    async def _on_assist_click(self, interaction: discord.Interaction):
        # Only the designated partner can use this button; the actor and bystanders
        # are politely rejected so the help is opt-in by the right person.
        if interaction.user.id != self.helper_user_id:
            await interaction.response.send_message(
                "這個協助按鈕不是給你的。",
                ephemeral=True,
            )
            return
        if self.used:
            await interaction.response.send_message("這次擲骰已經結束了。", ephemeral=True)
            return
        assert self.helper_pc_id is not None
        if self.helper_pc_id in self.helpers:
            self.helpers.remove(self.helper_pc_id)
            self._assist_btn.style = discord.ButtonStyle.secondary
            self._assist_btn.label = "我來協助 (+2)"
        else:
            self.helpers.append(self.helper_pc_id)
            self._assist_btn.style = discord.ButtonStyle.success
            self._assist_btn.label = "已加入協助 (+2)"
        await interaction.response.edit_message(view=self)


class ChoiceView(discord.ui.View):
    """Buttons for Tier-B candidate methods or Tier-C clarifying options."""

    def __init__(self, allowed_user_id: int, labels: list[str], on_choice: OnChoice, *, timeout: float | None = 300):
        super().__init__(timeout=timeout)
        self.allowed_user_id = allowed_user_id
        self.on_choice = on_choice
        self.used = False
        for i, label in enumerate(labels[:5]):
            btn = discord.ui.Button(label=_truncate(label), style=discord.ButtonStyle.secondary, row=i // 5)
            btn.callback = self._make_cb(label)
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _is_allowed_channel_id(interaction.channel_id):
            return True
        await _reject_disallowed_channel(interaction)
        return False

    def _make_cb(self, label: str):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.allowed_user_id:
                await interaction.response.send_message("這些選項不是給你的。", ephemeral=True)
                return
            if self.used:
                await interaction.response.send_message("你已經選過了。", ephemeral=True)
                return
            self.used = True
            for child in self.children:
                child.disabled = True
            self.stop()
            await self.on_choice(interaction, label)
        return _cb


def _truncate(s: str, n: int = 80) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
