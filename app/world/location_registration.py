"""AI-backed location registration and stable scene-card persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..ai import orchestrator
from ..ai.schemas import LocationCard
from ..content import scenario
from ..db import store
from ..logging_setup import get_logger, truncate

if TYPE_CHECKING:
    from ..state.game_state import GameState

log = get_logger("location_registration")


@dataclass
class LocationRegistrationRequest:
    requested_name: str
    source: str
    state: "GameState | None" = None
    location_id: str | None = None
    canonical_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    authored_notes: str = ""
    parent: str | None = None
    connects: list[str] = field(default_factory=list)
    player_text: str = ""
    # When set, the card is taken verbatim from authored content instead of being built by
    # the LLM — used for seed locations so /start does no per-location AI round-trips.
    authored_card: dict | None = None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        v = str(value).strip()
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _current_location_label(state: "GameState | None") -> str:
    if state is None:
        return ""
    loc = store.get_entity_by_id(state.current_location_id)
    return (loc or {}).get("name") or state.scene.title


def _recent_events(state: "GameState | None") -> list[str]:
    if state is None:
        return []
    lines: list[str] = []
    for event in state.event_log[-6:]:
        actor = getattr(event, "actor_name", "")
        summary = getattr(event, "summary", "")
        narration = getattr(event, "narration", "")
        line = f"- {actor}: {summary}" if actor else f"- {summary}"
        if narration:
            line += f"\n  prose: {truncate(narration, 180)}"
        lines.append(line)
    return lines


def _existing_locations() -> list[dict]:
    return [
        {
            "id": loc.get("id"),
            "name": loc.get("name"),
            "aliases": loc.get("aliases") or [],
        }
        for loc in store.get_locations()
    ]


def _request_payload(req: LocationRegistrationRequest) -> dict:
    return {
        "requested_name": req.requested_name,
        "canonical_name": req.canonical_name or "",
        "source": req.source,
        "current_location": _current_location_label(req.state),
        "authored_notes": req.authored_notes,
        "aliases": req.aliases,
        "parent": req.parent or "",
        "connects": req.connects,
        "player_text": req.player_text,
        "existing_locations": _existing_locations(),
        "recent_events": _recent_events(req.state),
    }


def _anchor_id(req: LocationRegistrationRequest) -> str | None:
    if req.state is None:
        return None
    return req.state.current_location_id


def _card_from_stored(card: dict) -> LocationCard:
    return LocationCard.model_validate(card)


def _authored_card(req: LocationRegistrationRequest) -> LocationCard:
    """Build a LocationCard from authored content, no LLM. Falls back to the request's
    canonical name / aliases / notes for any field the author left blank."""
    data = dict(req.authored_card or {})
    data.setdefault("canonical_name", req.canonical_name or req.requested_name)
    if req.aliases:
        data["aliases"] = _dedupe([*req.aliases, *data.get("aliases", [])])
    if not data.get("base_summary"):
        data["base_summary"] = req.authored_notes or data["canonical_name"]
    return LocationCard.model_validate(data)


def _authored_notes(defn: dict) -> str:
    parts = [str(defn.get("notes") or "").strip()]
    scene_def = scenario.scene_by_id(defn.get("id", ""))
    if scene_def and scene_def.get("summary"):
        parts.append(str(scene_def["summary"]).strip())
    return "\n".join(p for p in parts if p)


async def register_location_with_card(
    req: LocationRegistrationRequest,
) -> tuple[dict, LocationCard]:
    """Resolve/create a location, generate a stable card if needed, and persist both."""
    requested = req.requested_name.strip()
    if not requested:
        raise ValueError("location registration requires a requested_name")

    loc = store.get_entity_by_id(req.location_id) if req.location_id else None
    if loc is None:
        loc = store.find_location(req.canonical_name or requested)

    if loc is not None:
        loc = store.ensure_location_coordinates(
            loc["id"], anchor_id=_anchor_id(req), coord_parent=req.parent
        ) or loc
        existing_card = store.get_location_card(loc["id"])
        if existing_card:
            return loc, _card_from_stored(existing_card)

    if req.authored_card is not None:
        card = _authored_card(req)
        card_state = "authored"
    else:
        card, card_state = await orchestrator.build_location_card(_request_payload(req))
    name = (req.canonical_name or requested).strip()
    aliases = _dedupe([*req.aliases, *card.aliases])
    flags: dict = dict((loc or {}).get("flags") or {})
    if req.connects:
        flags["connects"] = list(req.connects)
    if req.parent:
        flags["parent"] = req.parent
    flags["terrain_modifier"] = card.terrain_modifier

    if loc is None:
        loc = store.register_location(
            name,
            location_id=req.location_id,
            aliases=aliases,
            notes=req.authored_notes,
            flags=flags or None,
            coordinate_anchor_id=_anchor_id(req),
            coord_parent=req.parent,
        )
    else:
        store.upsert_entity(
            id=loc["id"],
            scene_id=loc["scene_id"],
            kind=loc["kind"],
            name=loc["name"],
            aliases=_dedupe([*(loc.get("aliases") or []), *aliases]),
            status=loc["status"],
            location_id=loc.get("location_id"),
            disposition=loc.get("disposition"),
            flags=flags,
            notes=loc.get("notes", ""),
            first_seen_event_id=loc.get("first_seen_event_id"),
        )
        loc = store.ensure_location_coordinates(
            loc["id"], anchor_id=_anchor_id(req), coord_parent=req.parent
        ) or loc
        loc = store.get_entity_by_id(loc["id"]) or loc

    canonical = req.canonical_name or loc["name"]
    base_summary = card.base_summary or req.authored_notes or loc.get("notes") or canonical
    store.upsert_location_card(
        location_id=loc["id"],
        canonical_name=canonical,
        aliases=_dedupe([*(loc.get("aliases") or []), *aliases]),
        base_summary=base_summary,
        sensory_anchors=card.sensory_anchors,
        visual_landmarks=card.visual_landmarks,
        interactive_features=card.interactive_features,
        discoverables=card.discoverables,
        hazards=card.hazards,
        soft_hooks=card.soft_hooks,
        exits_hint=card.exits_hint,
        mood=card.mood,
        generated_source=f"{req.source}:{card_state}",
    )
    store.set_base_summary(loc["id"], base_summary)
    log.info(
        "location registered/carded source=%s id=%s name=%s state=%s",
        req.source,
        loc["id"],
        loc["name"],
        card_state,
    )
    return loc, card


async def ensure_seed_location_cards(
    state: "GameState | None",
    defs: list[dict],
) -> None:
    for defn in defs:
        await register_location_with_card(
            LocationRegistrationRequest(
                requested_name=str(defn["name"]),
                canonical_name=str(defn["name"]),
                source="authored_seed",
                state=state,
                location_id=defn.get("id"),
                aliases=list(defn.get("aliases") or []),
                authored_notes=_authored_notes(defn),
                parent=defn.get("parent"),
                connects=list(defn.get("connects") or []),
                authored_card=defn.get("card"),
            )
        )


async def promote_location_mention_with_card(
    scene_id: str | None,
    name: str,
    state: "GameState | None",
) -> str | None:
    if not name or not name.strip():
        return None
    loc, _ = await register_location_with_card(
        LocationRegistrationRequest(
            requested_name=name,
            source="mention_promote",
            state=state,
        )
    )
    store.clear_mention(scene_id, name)
    return loc["id"]
