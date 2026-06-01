"""Interactive components: the server-authoritative dice button (§9.3) and the A/B/C
method-choice buttons (§8.3 / §9.1).

The dice button is a one-shot trigger: it never rolls on the client, it just asks the
server to reveal an already-decided result, and it disables itself after one use so a
player can't re-roll.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

OnRoll = Callable[[discord.Interaction], Awaitable[None]]
OnChoice = Callable[[discord.Interaction, str], Awaitable[None]]


class RollView(discord.ui.View):
    """A single 🎲 button restricted to one player, usable exactly once."""

    def __init__(self, allowed_user_id: int, on_roll: OnRoll, *, timeout: float | None = 300):
        super().__init__(timeout=timeout)
        self.allowed_user_id = allowed_user_id
        self.on_roll = on_roll
        self.used = False

    @discord.ui.button(label="擲骰", emoji="🎲", style=discord.ButtonStyle.primary)
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.allowed_user_id:
            await interaction.response.send_message("這不是你的擲骰。", ephemeral=True)
            return
        if self.used:
            await interaction.response.send_message("這次擲骰已經完成了。", ephemeral=True)
            return
        self.used = True
        button.disabled = True
        self.stop()
        await self.on_roll(interaction)


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
