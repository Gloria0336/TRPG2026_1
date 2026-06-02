import pytest

from app.ai import orchestrator
from app.content import quest_taxonomy
from app.db import store
from app.engine.types import ResolutionResult, ResultBand, ResultKind
from app.state import game_state


def _seed(**overrides):
    base = {
        "seed_id": "dawnbridge",
        "giver": "老佩林",
        "title_hint": "晨橋商隊",
        "premise": "商隊三天前在東路失蹤。",
        "objective_hint": "前往東路查明商隊下落。",
        "reward_hint": "50 金幣",
        "acceptance_mode": "direct_accept",
        "required_check": None,
        "known_constraints": "民兵不願搜救。",
        "named_entities": ["老佩林"],
        "locations": ["東路"],
        "stakes": "商隊成員可能遇害。",
        "tags": {"source": "npc_commission", "risk_level": "moderate"},
        "dedupe_key": "tavern:perrin:dawnbridge_caravan",
    }
    base.update(overrides)
    return base


def test_quest_store_dedupes_and_accepts():
    q1 = store.upsert_quest_seed(
        dedupe_key="same", seed=_seed(), scene_id="tavern", giver="老佩林",
    )
    q2 = store.upsert_quest_seed(
        dedupe_key="same", seed=_seed(title_hint="晨橋商隊更新"), scene_id="tavern", giver="老佩林",
    )

    assert q1["id"] == q2["id"]
    assert len(store.list_quests(scene_id="tavern")) == 1

    accepted = store.accept_quest(q1["id"])
    assert accepted["status"] == "accepted"
    assert accepted["detail_state"] == "details_degraded"
    assert accepted["details"]["title"] == "晨橋商隊更新"


@pytest.mark.asyncio
async def test_narrate_with_quest_parses_gm_seed(monkeypatch):
    gs = game_state.reset_state(channel_id=1)
    result = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_bram",
        actor_name="Bram Ironwood",
        summary="Persuasion check vs DC 13: SUCCESS",
        success=True,
        band=ResultBand.SUCCESS,
        target_name="老佩林",
        raw_text="詢問老佩林是否有委託",
        topic="委託",
    )

    async def fake_chat(*args, **kwargs):
        return """
        {
          "prose": "老佩林壓低聲音，請你們幫忙找回失蹤的晨橋商隊。",
          "quest_offer": {
            "seed_id": "dawnbridge",
            "giver": "老佩林",
            "title_hint": "晨橋商隊",
            "premise": "商隊三天前在東路失蹤。",
            "objective_hint": "前往東路查明商隊下落。",
            "reward_hint": "50 金幣",
            "acceptance_mode": "direct_accept",
            "required_check": null,
            "known_constraints": "民兵不願搜救。",
            "named_entities": ["老佩林"],
            "locations": ["東路"],
            "stakes": "商隊成員可能遇害。",
            "tags": {"source": "npc_commission", "risk_level": "moderate"},
            "dedupe_key": "tavern:perrin:dawnbridge_caravan"
          }
        }
        """

    monkeypatch.setattr(orchestrator, "_ai_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "_chat", fake_chat)

    prose, seed = await orchestrator.narrate_with_quest(gs, result)

    assert "晨橋商隊" in prose
    assert seed is not None
    assert seed.giver == "老佩林"
    assert seed.tags["risk_level"] == "moderate"


@pytest.mark.asyncio
async def test_quest_agent_degrades_from_seed_when_ai_disabled(monkeypatch):
    gs = game_state.reset_state(channel_id=1)
    monkeypatch.setattr(orchestrator, "_ai_enabled", lambda: False)

    details, state = await orchestrator.build_quest_details(gs, _seed())

    assert state == "details_degraded"
    assert details["title"] == "晨橋商隊"
    assert details["tags"]["source"] == quest_taxonomy.DEFAULT_QUEST_TAGS["source"]
    assert details["reward"] == "50 金幣"


def test_dashboard_hides_details_until_accepted():
    gs = game_state.reset_state(channel_id=1)
    quest = store.upsert_quest_seed(
        dedupe_key="same", seed=_seed(), scene_id="tavern", giver="老佩林",
    )
    store.set_quest_details(quest["id"], {
        "title": "晨橋商隊",
        "giver": "老佩林",
        "objective": "查明商隊下落。",
        "known_info": ["三天前出發。"],
        "details": ["兩輛貨車。"],
        "next_steps": ["前往東路。"],
        "success_conditions": ["找到倖存者。"],
        "failure_risks": ["線索變冷。"],
        "reward": "50 金幣",
        "tags": {"source": "npc_commission"},
    })

    hidden = gs.dashboard_view()["quests"][0]
    assert "details" not in hidden

    store.accept_quest(quest["id"])
    shown = gs.dashboard_view()["quests"][0]
    assert shown["status"] == "accepted"
    assert shown["details"]["known_info"] == ["三天前出發。"]
