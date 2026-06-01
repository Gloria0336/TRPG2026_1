"""Narration guard — enforces §4.0 / §8.0 ("AI never touches numbers") as code.

The narrator prompt already says "don't invent numbers / DCs / dice". This module
turns that promise into a regex-based post-check so a hallucination is caught at
the engine boundary instead of leaking to players.

What we reject:
1. Explicit mechanic leakage — DC reveals, dice notation, modifier phrases.
2. Save mentions on non-SAVE results (the narrator inventing a saving throw).
3. Damage / healing numbers that don't match `result.damage` / `result.healing`.

What we DON'T reject (deliberately):
- Incidental Chinese numerals like "兩三隻哥布林", "走了幾百步" — these are fiction.
- Numbers that DO appear in the result's deltas (e.g. "剩 12 點生命值" when a
  delta literally said so) — the engine already wrote that number, narrator can echo.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..engine.types import ResolutionResult, ResultKind


# ───────────────────────── mechanic-leak patterns ─────────────────────────
# Bare "DC 15", "DC15", "難度等級 15". The narrator should describe difficulty as
# fiction (險峻、險峻、千鈞一髮), never reveal the literal anchor.
_DC_REVEAL = re.compile(r"(?:\bDC\s*\d+|難度等級\s*\d+)", re.IGNORECASE)

# Dice notation: d20, 1d6, 2d8+3 etc. Must match a digit-then-d-then-digit token; we
# anchor on the d so words like "DnD"/"d&d"/random "do" don't trigger.
_DICE_NOTATION = re.compile(r"\b\d*[dD]\d{1,3}(?:\s*[+-]\s*\d+)?\b")

# Modifier-talk: "+3 加值 / +2 修正 / +5 bonus". Narrator should never quote the math.
_MODIFIER_TALK = re.compile(
    r"[+-]\s*\d+\s*(?:加值|修正|調整|modifier|bonus|加成)",
    re.IGNORECASE,
)

# Dice-talk phrases ("擲骰", "擲出 17"). Mechanical reveal, not fiction.
_DICE_TALK = re.compile(r"(?:擲(?:出|了|到)?\s*\d+|擲骰|骰子\s*\d+)")


def _has(pattern: re.Pattern[str], prose: str) -> bool:
    return pattern.search(prose) is not None


# ───────────────────────── number-mismatch detection ─────────────────────────
# Damage-shaped phrases. Captured group is the number; we compare it to the result's
# actual damage. If the prose mentions damage that the engine didn't apply, that's a
# fabricated mechanical outcome.
_DAMAGE_PHRASES = [
    re.compile(r"(\d+)\s*點?\s*傷害"),
    re.compile(r"造成\s*(\d+)\s*點"),
    re.compile(r"扣(?:除|了)?\s*(\d+)\s*(?:點|HP|生命值)"),
    re.compile(r"失去\s*(\d+)\s*(?:點\s*)?(?:HP|生命值)"),
]

_HEAL_PHRASES = [
    re.compile(r"恢復\s*(\d+)\s*(?:點|HP|生命值)"),
    re.compile(r"治療\s*(\d+)\s*點"),
    re.compile(r"回復\s*(\d+)\s*(?:點|HP|生命值)"),
]


def _numbers_in_deltas(deltas: Iterable[str]) -> set[int]:
    """Numbers the engine already published in plain text — narrator may echo them."""
    out: set[int] = set()
    for d in deltas:
        for m in re.finditer(r"\b(\d+)\b", d):
            try:
                out.add(int(m.group(1)))
            except ValueError:
                pass
    return out


def _check_number_phrases(
    prose: str,
    patterns: list[re.Pattern[str]],
    allowed: set[int],
) -> list[str]:
    findings: list[str] = []
    for pat in patterns:
        for m in pat.finditer(prose):
            try:
                n = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if n in allowed:
                continue
            findings.append(f"{pat.pattern}→{n} (not in result)")
    return findings


# ───────────────────────── public API ─────────────────────────
def find_violations(prose: str, result: ResolutionResult) -> list[str]:
    """Return a list of guard violation tags; empty list means the prose is clean.

    Each tag is a short string suitable for logging and for inclusion in a retry
    reminder so the model can correct itself.
    """
    if not prose:
        return []

    violations: list[str] = []
    if _has(_DC_REVEAL, prose):
        violations.append("explicit DC/難度等級 number revealed")
    if _has(_DICE_NOTATION, prose):
        violations.append("dice notation (d20 / 1d6 / 2d8) revealed")
    if _has(_MODIFIER_TALK, prose):
        violations.append("modifier/bonus number revealed")
    if _has(_DICE_TALK, prose):
        violations.append("mentions rolling dice / 擲骰 explicitly")

    # "豁免" outside a SAVE result is a fabricated mechanical event.
    if result.kind is not ResultKind.SAVE and "豁免" in prose:
        violations.append("mentions 豁免 (saving throw) on a non-save result")

    # Damage-shaped numbers must match result.damage (or appear in deltas the engine
    # already wrote, e.g. "Goblin takes 6 damage" → narrator echoing "6 點傷害" is OK).
    allowed_numbers = _numbers_in_deltas(result.deltas)
    if result.damage:
        allowed_numbers.add(int(result.damage))
    dmg_findings = _check_number_phrases(prose, _DAMAGE_PHRASES, allowed_numbers)
    if dmg_findings:
        violations.extend(f"damage number mismatch: {f}" for f in dmg_findings)

    # Healing-shaped numbers must match result.healing or be in deltas.
    heal_allowed = _numbers_in_deltas(result.deltas)
    if result.healing:
        heal_allowed.add(int(result.healing))
    heal_findings = _check_number_phrases(prose, _HEAL_PHRASES, heal_allowed)
    if heal_findings:
        violations.extend(f"healing number mismatch: {f}" for f in heal_findings)

    return violations


def violation_reminder(violations: list[str]) -> str:
    """Build a strict reminder to append on a retry so the model self-corrects."""
    if not violations:
        return ""
    lines = "\n".join(f"  - {v}" for v in violations)
    return (
        "GUARD: your previous reply contained the following rule violations:\n"
        f"{lines}\n"
        "Rewrite the narration WITHOUT these. Never reveal DCs, dice, modifiers, "
        "saving throws, or numbers that the engine did not produce. Keep the same "
        "fictional outcome — only purge the mechanical leaks."
    )
