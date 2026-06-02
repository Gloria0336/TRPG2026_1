"""Quest tag taxonomy and lifecycle constants."""
from __future__ import annotations

QUEST_STATUSES = ("available", "awaiting_check", "accepted", "completed", "failed", "expired")
QUEST_DETAIL_STATES = ("pending_agent", "ready", "details_degraded")
QUEST_ACCEPTANCE_MODES = ("direct_accept", "requires_check")

QUEST_TAG_AXES: dict[str, tuple[str, ...]] = {
    "source": (
        "npc_commission", "rumor", "guild_notice", "faction_order",
        "personal_goal", "world_event",
    ),
    "urgency": ("immediate", "timed", "open_ended", "downtime", "recurring"),
    "world_impact": ("personal", "local", "regional", "factional", "kingdom", "world"),
    "skill_need": (
        "social", "exploration", "combat", "stealth", "knowledge",
        "crafting", "survival", "magic", "mixed",
    ),
    "alignment_tendency": (
        "lawful", "good", "neutral", "chaotic", "evil", "factional", "ambiguous",
    ),
    "risk_level": ("trivial", "low", "moderate", "high", "deadly", "unknown"),
    "scale": ("solo", "party", "local_group", "settlement", "faction", "army"),
    "content_type": (
        "delivery", "escort", "investigation", "rescue", "bounty",
        "negotiation", "exploration", "dungeon", "defense", "sabotage",
        "recovery", "trade", "mystery", "mixed",
    ),
}

DEFAULT_QUEST_TAGS: dict[str, str] = {
    "source": "npc_commission",
    "urgency": "open_ended",
    "world_impact": "local",
    "skill_need": "mixed",
    "alignment_tendency": "neutral",
    "risk_level": "unknown",
    "scale": "party",
    "content_type": "mixed",
}


def normalize_tags(tags: dict | None) -> dict[str, str]:
    """Coerce arbitrary tag data into the fixed eight-axis taxonomy."""
    out = dict(DEFAULT_QUEST_TAGS)
    if not isinstance(tags, dict):
        return out
    for axis, allowed in QUEST_TAG_AXES.items():
        value = tags.get(axis)
        if isinstance(value, str) and value in allowed:
            out[axis] = value
    return out
