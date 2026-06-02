"""Step 3 — debounced auto-registration of AI-invented entities.

A brand-new place/person the narrator mentions must be named `mention_promote_threshold`
times before it becomes a real record. This stops one-off background flavour from bloating
the world, while still letting genuinely recurring elements persist (so the world stops
snapping back to scripted scenes). A player's explicit travel target bypasses this.
"""
from app.config import settings
from app.db import store


def test_mention_below_threshold_does_not_register():
    for _ in range(settings.mention_promote_threshold - 1):
        c = store.record_mention("tavern", "神秘祭壇", "location")
    assert c == settings.mention_promote_threshold - 1
    assert store.find_location("神秘祭壇") is None


def test_mention_reaching_threshold_promotes():
    name, scope = "神秘祭壇", "tavern"
    count = 0
    for _ in range(settings.mention_promote_threshold):
        count = store.record_mention(scope, name, "location")
    assert count == settings.mention_promote_threshold
    ent_id = store.promote_mention(scope, name, "location")
    assert ent_id is not None
    assert store.find_location(name) is not None
    # Tally row is cleared on promotion.
    assert store.record_mention(scope, name, "location") == 0  # now a known entity


def test_player_travel_target_bypasses_threshold():
    # resolve_or_register_location is the player path: one reference is enough.
    loc = store.resolve_or_register_location("私釀酒窖")
    assert loc is not None and store.find_location("私釀酒窖") is not None


def test_known_entity_is_not_tallied():
    store.seed_entities("tavern", [
        {"id": "ent_perrin", "kind": "person", "name": "老佩林", "aliases": ["佩林"]},
    ])
    # Already-registered → record_mention returns 0 (nothing to debounce).
    assert store.record_mention("tavern", "老佩林", "person") == 0
    assert store.record_mention("tavern", "佩林", "person") == 0


def test_tally_is_scoped_per_location():
    store.record_mention("tavern", "破舊神龕", "location")
    store.record_mention("tavern", "破舊神龕", "location")
    # A different scope counts independently.
    assert store.record_mention("east_road", "破舊神龕", "location") == 1
