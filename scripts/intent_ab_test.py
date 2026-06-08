"""Run a 10-case A/B evaluation for the intent parser models.

Usage:
    python -m scripts.intent_ab_test

The harness reuses the production intent parser prompt and context builder:
`prompts.INTENT_SYSTEM` + `prompts.intent_context(...)`. It does not call
`orchestrator.interpret(...)` because this script needs to force two model slugs
against the same input and separately record JSON/schema/fallback behavior.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

# Windows consoles may default to cp950; keep report paths and Chinese snippets readable.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from app.ai import orchestrator, prompts
from app.ai.schemas import IntentParse
from app.config import settings
from app.content import scenario
from app.db import store
from app.state import game_state


DEFAULT_MODEL_A = "openai/gpt-4o-mini"
DEFAULT_MODEL_B = "deepseek/deepseek-v4-flash"
DEFAULT_OUTPUT_DIR = Path("logs") / "intent_ab"


@dataclass(frozen=True)
class Expected:
    tier: str | None = None
    is_attack: bool | None = None
    needs_check: bool | None = None
    implausible: bool | None = None
    approach_any: tuple[str, ...] = ()
    target_contains_any: tuple[str, ...] = ()
    action_contains_any: tuple[str, ...] = ()
    topic_contains_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    actor_id: str
    message: str
    scene_id: str = "tavern"
    clarification: list[dict[str, str]] = field(default_factory=list)
    expected: Expected = field(default_factory=Expected)
    note: str = ""


CASES: tuple[Case, ...] = (
    Case(
        id="tavern_social_persuasion",
        actor_id="pc_lyra",
        message="我請老佩林喝一杯，說服他告訴我商隊最後往哪裡走。",
        expected=Expected(tier="A", needs_check=True, approach_any=("persuasion",)),
        note="Clear social action against a friendly tavern NPC.",
    ),
    Case(
        id="tavern_hidden_listener",
        actor_id="pc_bram",
        message="我掃視酒館，找找有沒有人偷聽我們談商隊的事。",
        expected=Expected(tier="A", needs_check=True, approach_any=("perception", "investigation")),
        note="Searching for hidden information should need a check.",
    ),
    Case(
        id="tavern_weird_literal",
        actor_id="pc_bram",
        message="我舔一下那枚銅幣，確認有沒有毒或奇怪味道。",
        expected=Expected(tier="A", implausible=False),
        note="Weird but literal action; anti-paternalism should keep it playable.",
    ),
    Case(
        id="tavern_implausible_gear",
        actor_id="pc_bram",
        message="我引爆背包裡的 C4，把酒館地下金庫炸開。",
        expected=Expected(tier="A", implausible=True),
        note="False premise: actor inventory has no C4 and the vault is not established.",
    ),
    Case(
        id="tavern_clear_travel",
        actor_id="pc_lyra",
        message="我帶大家往東路前進，沿著商隊留下的方向走。",
        expected=Expected(tier="A", needs_check=False, implausible=False),
        note="Pure travel declaration should not be implausible.",
    ),
    Case(
        id="tavern_unclear",
        actor_id="pc_bram",
        message="呃……那個……我先看看。",
        expected=Expected(tier="C"),
        note="Underspecified intent should ask a short GM follow-up.",
    ),
    Case(
        id="clarification_converges",
        actor_id="pc_bram",
        message="沿著車轍往東路追。",
        clarification=[
            {"player": "我要去找商隊。", "gm": "你想往哪個方向找，或用什麼線索追？"},
        ],
        expected=Expected(tier="A"),
        note="Clarification history should converge instead of asking again.",
    ),
    Case(
        id="road_wagon_search",
        actor_id="pc_bram",
        scene_id="east_road",
        message="我檢查破損的貨車，找找商隊被拖走的線索。",
        expected=Expected(tier="A", needs_check=True, approach_any=("investigation", "perception")),
        note="Object investigation in the east road scene.",
    ),
    Case(
        id="road_track_survival",
        actor_id="pc_lyra",
        scene_id="east_road",
        message="我沿著地上的足跡和車轍追蹤牠們往哪裡去了。",
        expected=Expected(tier="A", needs_check=True, approach_any=("survival", "perception", "investigation")),
        note="Tracking should usually map to survival/perception/investigation.",
    ),
    Case(
        id="ambush_attack",
        actor_id="pc_bram",
        scene_id="ambush",
        message="我拔劍攻擊最近的哥布林。",
        expected=Expected(tier="A", is_attack=True, needs_check=True, action_contains_any=("attack", "攻擊")),
        note="Combat attack must set is_attack.",
    ),
)


def _build_state(case: Case) -> game_state.GameState:
    gs = game_state.reset_state(channel_id=0)
    if case.scene_id != gs.scene.id:
        scene_def = scenario.scene_by_id(case.scene_id)
        if scene_def is None:
            raise ValueError(f"unknown scene_id for {case.id}: {case.scene_id}")
        gs.goto_scene(scene_def)
        if case.scene_id == "ambush":
            gs.start_scene_combat()
    return gs


def _expected_checks(parsed: IntentParse, expected: Expected) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if expected.tier is not None:
        checks["tier"] = parsed.tier == expected.tier
    if expected.is_attack is not None:
        checks["is_attack"] = parsed.is_attack is expected.is_attack
    if expected.needs_check is not None:
        checks["needs_check"] = parsed.needs_check is expected.needs_check
    if expected.implausible is not None:
        checks["implausible"] = parsed.implausible is expected.implausible
    if expected.approach_any:
        approach = (parsed.approach or "").lower()
        checks["approach"] = approach in expected.approach_any
    if expected.target_contains_any:
        target = parsed.target or ""
        checks["target"] = any(part in target for part in expected.target_contains_any)
    if expected.action_contains_any:
        action = (parsed.action or "").lower()
        checks["action"] = any(part.lower() in action for part in expected.action_contains_any)
    if expected.topic_contains_any:
        topic = parsed.topic or ""
        checks["topic"] = any(part in topic for part in expected.topic_contains_any)
    return checks


async def _run_one(model: str, case: Case) -> dict[str, Any]:
    gs = _build_state(case)
    actor = gs.characters[case.actor_id]
    user_prompt = prompts.intent_context(gs, actor, case.message, clarification=case.clarification)
    started = time.perf_counter()
    raw = ""
    extracted = ""
    fallback = None
    parsed: IntentParse | None = None
    error: str | None = None

    try:
        raw = await orchestrator._chat(  # noqa: SLF001 - intentional A/B harness.
            model,
            prompts.INTENT_SYSTEM,
            user_prompt,
            json_mode=True,
            max_tokens=300,
        )
        extracted = orchestrator._extract_json(raw)  # noqa: SLF001
        parsed = IntentParse.model_validate_json(extracted)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        fallback = orchestrator._offline_parse(gs, actor, case.message)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        fallback = orchestrator._offline_parse(gs, actor, case.message)  # noqa: SLF001

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    parsed_dump = parsed.model_dump() if parsed else None
    checks = _expected_checks(parsed, case.expected) if parsed else {}
    return {
        "case_id": case.id,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "schema_ok": parsed is not None,
        "fallback_used": fallback is not None,
        "expected_pass": bool(checks) and all(checks.values()) if parsed else False,
        "expected_checks": checks,
        "parsed": parsed_dump,
        "fallback": fallback.to_dict() if fallback else None,
        "raw": raw,
        "extracted": extracted,
        "error": error,
    }


def _latency_stats(rows: list[dict[str, Any]]) -> dict[str, int | None]:
    vals = [int(r["elapsed_ms"]) for r in rows]
    if not vals:
        return {"median_ms": None, "p95_ms": None, "max_ms": None}
    sorted_vals = sorted(vals)
    p95_index = max(0, min(len(sorted_vals) - 1, int(round((len(sorted_vals) - 1) * 0.95))))
    return {
        "median_ms": int(statistics.median(sorted_vals)),
        "p95_ms": sorted_vals[p95_index],
        "max_ms": max(sorted_vals),
    }


def _summarize(results: list[dict[str, Any]], models: tuple[str, str]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for model in models:
        rows = [r for r in results if r["model"] == model]
        by_model[model] = {
            "calls": len(rows),
            "schema_ok": sum(1 for r in rows if r["schema_ok"]),
            "fallback_used": sum(1 for r in rows if r["fallback_used"]),
            "expected_pass": sum(1 for r in rows if r["expected_pass"]),
            **_latency_stats(rows),
        }

    disagreements = []
    for case in CASES:
        pair = [r for r in results if r["case_id"] == case.id]
        if len(pair) != len(models):
            continue
        parsed = {r["model"]: r.get("parsed") for r in pair}
        key_fields = {
            model: {
                k: (parsed[model] or {}).get(k)
                for k in ("tier", "action", "target", "approach", "topic", "needs_check", "is_attack", "goal", "steps", "feasibility", "side_effects", "implausible")
            }
            for model in models
        }
        if key_fields[models[0]] != key_fields[models[1]]:
            disagreements.append({"case_id": case.id, "fields": key_fields})

    return {"by_model": by_model, "disagreements": disagreements}


def _write_markdown(
    path: Path,
    *,
    started_at: str,
    models: tuple[str, str],
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    lines = [
        "# Intent Parser A/B Test Report",
        "",
        f"- Started at: `{started_at}`",
        f"- Prompt: `app.ai.prompts.INTENT_SYSTEM` + `app.ai.prompts.intent_context(...)`",
        f"- Cases: `{len(CASES)}` Traditional Chinese intent inputs",
        f"- Model A: `{models[0]}`",
        f"- Model B: `{models[1]}`",
        "",
        "## Summary",
        "",
        "| Model | Calls | Schema OK | Fallback | Expected Pass | Median ms | P95 ms | Max ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in models:
        row = summary["by_model"][model]
        lines.append(
            f"| `{model}` | {row['calls']} | {row['schema_ok']} | {row['fallback_used']} | "
            f"{row['expected_pass']} | {row['median_ms']} | {row['p95_ms']} | {row['max_ms']} |"
        )

    a, b = models
    row_a = summary["by_model"][a]
    row_b = summary["by_model"][b]
    better_schema = a if row_a["schema_ok"] >= row_b["schema_ok"] else b
    better_expected = a if row_a["expected_pass"] >= row_b["expected_pass"] else b
    better_latency = a if (row_a["median_ms"] or 0) <= (row_b["median_ms"] or 0) else b
    recommendation = (
        f"Keep `{a}` as the default until `{b}` is retested with prompt/provider tuning."
        if better_schema == a and better_expected == a and better_latency == a
        else f"`{b}` is a plausible candidate, but review the per-case disagreements before switching defaults."
    )
    lines += [
        "",
        "## Interpretation",
        "",
        f"- Schema stability leader: `{better_schema}`.",
        f"- Lightweight semantic contract leader: `{better_expected}`.",
        f"- Median latency leader: `{better_latency}`.",
        f"- Recommendation: {recommendation}",
    ]

    lines += [
        "",
        "## Case Results",
        "",
        "| Case | Note | Model | ms | Schema | Fallback | Expected | Parsed fields | Error |",
        "|---|---|---|---:|---|---|---|---|---|",
    ]
    notes = {case.id: case.note for case in CASES}
    for result in results:
        parsed = result.get("parsed") or {}
        fields = {
            k: parsed.get(k)
            for k in ("tier", "action", "target", "approach", "topic", "needs_check", "is_attack", "goal", "steps", "feasibility", "side_effects", "implausible")
            if parsed.get(k) is not None
        }
        field_text = json.dumps(fields, ensure_ascii=False)
        error = (result.get("error") or "").replace("\n", " ")[:220]
        lines.append(
            f"| `{result['case_id']}` | {notes[result['case_id']]} | `{result['model']}` | "
            f"{result['elapsed_ms']} | {result['schema_ok']} | {result['fallback_used']} | "
            f"{result['expected_pass']} | `{field_text}` | {error} |"
        )

    lines += ["", "## Model Disagreements", ""]
    disagreements = summary["disagreements"]
    if not disagreements:
        lines.append("No key-field disagreements.")
    else:
        for item in disagreements:
            lines.append(f"### `{item['case_id']}`")
            for model, fields in item["fields"].items():
                lines.append(f"- `{model}`: `{json.dumps(fields, ensure_ascii=False)}`")
            lines.append("")

    lines += [
        "",
        "## Notes",
        "",
        "- `Fallback` means the remote call, JSON extraction, or Pydantic schema validation failed and the local offline parser was used for that row.",
        "- `Expected Pass` is a lightweight contract score for this benchmark case, not a full semantic proof.",
        "- Full raw model replies are stored in the adjacent JSON file.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


async def _run(args: argparse.Namespace) -> tuple[Path, Path]:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured; cannot run remote A/B calls.")

    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"intent_ab_{started_at}.json"
    md_path = output_dir / f"intent_ab_{started_at}.md"
    models = (args.model_a, args.model_b)

    original_db_path = settings.db_path
    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="trpg_intent_ab_", ignore_cleanup_errors=True) as tmp:
            settings.db_path = Path(tmp) / "world.db"
            store.close()
            try:
                for case in CASES:
                    for model in models:
                        print(f"[{case.id}] {model}")
                        results.append(await _run_one(model, case))
            finally:
                store.close()
    finally:
        settings.db_path = original_db_path
        store.close()
        await orchestrator.aclose()

    summary = _summarize(results, models)
    payload = {
        "started_at": started_at,
        "models": {"a": models[0], "b": models[1]},
        "prompt": {
            "system": "app.ai.prompts.INTENT_SYSTEM",
            "user_context": "app.ai.prompts.intent_context(...)",
            "json_mode": True,
            "temperature": 0.2,
            "max_tokens": 300,
        },
        "summary": summary,
        "cases": [
            {
                "id": case.id,
                "scene_id": case.scene_id,
                "actor_id": case.actor_id,
                "message": case.message,
                "clarification": case.clarification,
                "note": case.note,
            }
            for case in CASES
        ],
        "results": results,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(md_path, started_at=started_at, models=models, summary=summary, results=results)
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a 10-case intent parser A/B model test.")
    parser.add_argument("--model-a", default=DEFAULT_MODEL_A)
    parser.add_argument("--model-b", default=DEFAULT_MODEL_B)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    json_path, md_path = asyncio.run(_run(args))
    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    main()
