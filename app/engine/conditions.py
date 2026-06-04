"""Mechanical conditions / flags catalog (MVP — A & E class subset).

A condition is a *string id* attached to an entity. The resolution layer reads the
catalog to decide whether a check should skip / auto-succeed / auto-fail / gain
advantage / disadvantage before any d20 is rolled. The narrator is told which
condition is in effect so the prose stays consistent with the mechanic.

Scope (MVP, intentionally small):
- A class: target conditions that change how social/attack/skill checks resolve
  against the target (charmed, hypnotized, dominated, unconscious, frightened,
  poisoned, restrained, prone, paralyzed, stunned, blinded).
- E class: semantic / physical-feasibility flags that the intent layer reads to
  decide whether the action makes sense at all (target_unconscious, target_dead,
  out_of_range, already_resolved).

Out of scope here (deferred to a later pass):
- B class (actor self buffs/debuffs: bless, guidance, exhaustion levels …)
- C class (location ambient: darkness, public_setting …)
- D class (NPC dialog state: under_duress, lying, indebted_to …)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CheckOutcome(str, Enum):
    """How a pre-roll gate decides to short-circuit a check."""

    ROLL = "roll"                  # nothing special — proceed to d20
    AUTO_SUCCESS = "auto_success"  # skip d20, treat as SUCCESS
    AUTO_FAIL = "auto_fail"        # skip d20, treat as FAILURE
    AUTO_CRIT = "auto_crit"        # for attacks at melee range vs paralyzed/unconscious


# ───────────────────────── Condition ids (string constants) ─────────────────────────
# A class — target receives these
CHARMED = "charmed"
HYPNOTIZED = "hypnotized"
DOMINATED = "dominated"
UNCONSCIOUS = "unconscious"
PARALYZED = "paralyzed"
STUNNED = "stunned"
FRIGHTENED = "frightened"
RESTRAINED = "restrained"
GRAPPLED = "grappled"
PRONE = "prone"
BLINDED = "blinded"
DEAFENED = "deafened"

# B class — actor-side buffs/debuffs (modify the actor's OWN roll, not the target)
BLESSED = "blessed"                          # +1d4 to check & save
BANE = "bane"                                # -1d4 to check & save
GUIDANCE = "guidance"                        # +1d4 once
HEROISM = "heroism"                          # immune to frightened
ENHANCE_ABILITY = "enhance_ability"          # adv on chosen ability
POISONED = "poisoned"                        # disadv on attack & check
EXHAUSTED = "exhausted"                      # leveled (1-6); meta carries level
RAGING = "raging"                            # STR check adv (barbarian)

# D class — NPC dialog flags (read mostly by intent prompt & narration; some drop the
# check one degree of success via post-roll modifiers)
UNDER_DURESS = "under_duress"                # forced to talk; drops one degree (SUCCESS → FAILURE)
LYING = "lying"                              # insight DC -3 against this target
LOYAL_TO = "loyal_to"                        # parametric — see loyal_to() helper
INDEBTED_TO = "indebted_to"                  # parametric — see indebted_to() helper
NPC_DISTRACTED = "npc_distracted"            # adv on stealth/sleight against them

# E class — semantic / inferred from other state, used by intent + resolution gates
TARGET_UNCONSCIOUS = "target_unconscious"   # mirrors UNCONSCIOUS for the intent prompt
TARGET_DEAD = "target_dead"
ALREADY_RESOLVED = "already_resolved"       # social goal already achieved on this NPC


# ───────────────────────── Parametric helpers ─────────────────────────
# `loyal_to:ent_perrin` keeps the bound entity id in the condition id itself, so the
# bearer can hold multiple (loyal_to:guard_captain, loyal_to:queen) without colliding.
def loyal_to(ref: str) -> str:
    return f"{LOYAL_TO}:{ref}"


def indebted_to(ref: str) -> str:
    return f"{INDEBTED_TO}:{ref}"


def parse_parametric(condition_id: str) -> tuple[str, str | None]:
    """Split a parametric id like 'loyal_to:ent_perrin' → ('loyal_to', 'ent_perrin')."""
    if ":" in condition_id:
        base, _, ref = condition_id.partition(":")
        return base, ref or None
    return condition_id, None

# Categories of approach the gates care about.
SOCIAL_SKILLS = frozenset({"diplomacy", "deception", "intimidation", "perception", "performance"})
ATTACK_LIKE = frozenset({"attack", "melee", "ranged"})


@dataclass(frozen=True)
class ConditionEffect:
    """How one condition modifies a check, whether it lives on the target or the actor.

    `outcomes` and `advantage_against` describe what happens to checks made AGAINST
    a bearer of this condition (target-side, e.g. CHARMED).

    `actor_outcomes`, `actor_advantage`, `actor_bonus_dice`, `band_downgrade` describe
    what happens to checks made BY a bearer of this condition (actor-side, e.g. BLESSED).

    `requires_level` marks parametric leveled conditions (exhausted L1..L5); the catalog
    entry holds the base behaviour and resolution branches on the meta level.
    """

    label_zh: str
    description_zh: str
    # ── target-side (bearer is the target of the check) ──
    outcomes: dict[str, CheckOutcome] = field(default_factory=dict)
    advantage_against: dict[str, str] = field(default_factory=dict)
    # ── actor-side (bearer is the one rolling) ──
    actor_outcomes: dict[str, CheckOutcome] = field(default_factory=dict)
    actor_advantage: dict[str, str] = field(default_factory=dict)
    # +1d4 / -1d4 sized modifier rolled inline. Engine reads this and rolls one
    # extra die per active source (caps at one for now — bless/guidance don't stack).
    actor_bonus_dice: int = 0   # signed: +4 for bless/guidance, -4 for bane
    # If True this bearer's check is dropped one degree of success (PF2e), e.g.
    # CRIT_SUCCESS→SUCCESS, SUCCESS→FAILURE. Used by UNDER_DURESS so a coerced NPC's
    # success no longer comes clean (a cost then attaches once it lands on a failure band).
    band_downgrade: bool = False
    incapacitated: bool = False
    requires_level: bool = False


# Catalog. Only conditions Mendoza in this pass have entries; unknown ids are
# treated as inert (no effect), which keeps narrative-only flags from breaking
# anything when authors invent new ones.
CATALOG: dict[str, ConditionEffect] = {
    CHARMED: ConditionEffect(
        label_zh="魅惑",
        description_zh="對來源的社交檢定自動成功；不可被該來源敵對攻擊或威嚇。",
        outcomes={
            "diplomacy": CheckOutcome.AUTO_SUCCESS,
            "deception": CheckOutcome.AUTO_SUCCESS,
            "intimidation": CheckOutcome.AUTO_FAIL,
        },
    ),
    HYPNOTIZED: ConditionEffect(
        label_zh="催眠",
        description_zh="意識恍惚、對社交詢問直接回應；無法主動行動。",
        outcomes={
            "social": CheckOutcome.AUTO_SUCCESS,
            "attack": CheckOutcome.AUTO_CRIT,
        },
        incapacitated=True,
    ),
    DOMINATED: ConditionEffect(
        label_zh="支配",
        description_zh="完全聽令；任何下達的指令直接執行。",
        outcomes={
            "social": CheckOutcome.AUTO_SUCCESS,
        },
        incapacitated=True,
    ),
    UNCONSCIOUS: ConditionEffect(
        label_zh="昏迷",
        description_zh="毫無知覺；對其攻擊自動命中且 5ft 內為致命一擊，社交無效。",
        outcomes={
            "social": CheckOutcome.AUTO_FAIL,
            "attack": CheckOutcome.AUTO_CRIT,
        },
        incapacitated=True,
    ),
    PARALYZED: ConditionEffect(
        label_zh="麻痺",
        description_zh="無法動作；對其攻擊有優勢，近戰致命一擊。",
        outcomes={
            "attack": CheckOutcome.AUTO_CRIT,
        },
        advantage_against={"any": "advantage"},
        incapacitated=True,
    ),
    STUNNED: ConditionEffect(
        label_zh="震懾",
        description_zh="語無倫次、無法行動；對其攻擊有優勢。",
        advantage_against={"any": "advantage"},
        incapacitated=True,
    ),
    FRIGHTENED: ConditionEffect(
        label_zh="恐懼",
        description_zh="對來源有顯著不利；無法主動靠近來源。",
        advantage_against={"any": "advantage"},
    ),
    RESTRAINED: ConditionEffect(
        label_zh="束縛",
        description_zh="速度歸零；對其攻擊有優勢，其自身 DEX 豁免不利。",
        advantage_against={"any": "advantage"},
    ),
    GRAPPLED: ConditionEffect(
        label_zh="擒抱",
        description_zh="速度歸零，但仍可行動。",
    ),
    PRONE: ConditionEffect(
        label_zh="倒地",
        description_zh="近戰攻擊有優勢；遠程攻擊不利。",
        advantage_against={"attack": "advantage"},
    ),
    BLINDED: ConditionEffect(
        label_zh="失明",
        description_zh="視覺感知失效；對其攻擊有優勢。",
        advantage_against={"attack": "advantage"},
    ),
    DEAFENED: ConditionEffect(
        label_zh="耳聾",
        description_zh="聽覺感知失效。",
    ),
    # E class — semantic flags. They mirror conditions above so the intent prompt
    # can read them without inferring; resolution still goes through the same gate.
    TARGET_UNCONSCIOUS: ConditionEffect(
        label_zh="目標昏迷",
        description_zh="目標已昏迷；社交無效，攻擊自動命中。",
        outcomes={
            "social": CheckOutcome.AUTO_FAIL,
            "attack": CheckOutcome.AUTO_CRIT,
        },
    ),
    TARGET_DEAD: ConditionEffect(
        label_zh="目標死亡",
        description_zh="目標已死亡；所有針對它的社交動作自動失敗。",
        outcomes={
            "social": CheckOutcome.AUTO_FAIL,
            "any": CheckOutcome.AUTO_FAIL,
        },
    ),
    ALREADY_RESOLVED: ConditionEffect(
        label_zh="已招供",
        description_zh="此目標已就同類議題充分配合，再次社交無需檢定。",
        outcomes={
            "social": CheckOutcome.AUTO_SUCCESS,
        },
    ),
    # ── B class (actor-side) ──
    BLESSED: ConditionEffect(
        label_zh="祝福",
        description_zh="檢定與豁免額外滾 +1d4。",
        actor_bonus_dice=+4,
    ),
    GUIDANCE: ConditionEffect(
        label_zh="神引",
        description_zh="一次性 +1d4 加到非攻擊檢定。",
        actor_bonus_dice=+4,
    ),
    BANE: ConditionEffect(
        label_zh="災難",
        description_zh="檢定與豁免額外滾 -1d4。",
        actor_bonus_dice=-4,
    ),
    HEROISM: ConditionEffect(
        label_zh="英勇",
        description_zh="對恐懼免疫；勇氣高昂。",
    ),
    ENHANCE_ABILITY: ConditionEffect(
        label_zh="強化屬性",
        description_zh="指定屬性的所有檢定具備優勢。",
        actor_advantage={"any": "advantage"},
    ),
    POISONED: ConditionEffect(
        label_zh="中毒",
        description_zh="自身攻擊與檢定具備劣勢。",
        actor_advantage={"any": "disadvantage"},
    ),
    EXHAUSTED: ConditionEffect(
        label_zh="疲勞",
        description_zh="疲勞等級越高影響越重；L3 起所有 d20 不利、L6 死亡。",
        requires_level=True,
    ),
    RAGING: ConditionEffect(
        label_zh="狂暴",
        description_zh="力量檢定有優勢，對物理傷害抗性。",
        actor_advantage={"athletics": "advantage"},
    ),
    # ── D class (NPC dialog flags) ──
    UNDER_DURESS: ConditionEffect(
        label_zh="被脅迫",
        description_zh="在威脅下被迫配合：成功也帶有破綻或保留。",
        band_downgrade=True,
    ),
    LYING: ConditionEffect(
        label_zh="說謊",
        description_zh="正在說謊；對該目標的洞察檢定有優勢。",
        advantage_against={"perception": "advantage"},
    ),
    LOYAL_TO: ConditionEffect(
        label_zh="效忠",
        description_zh="對效忠對象不利的說服／威嚇自動失敗。",
        # Engine layer (not gate) decides whether the loyalty target overlaps the
        # ACTOR — catalog only marks the flag's presence for the prompt.
    ),
    INDEBTED_TO: ConditionEffect(
        label_zh="欠人情",
        description_zh="對欠人情對象的說服／請託更容易成功。",
    ),
    NPC_DISTRACTED: ConditionEffect(
        label_zh="分心",
        description_zh="注意力被分散；對其潛行與巧手有優勢。",
        advantage_against={"stealth": "advantage", "thievery": "advantage"},
    ),
}


def _category_for(approach: str | None, is_attack: bool) -> str:
    """Map (approach, is_attack) → a category key used to look up outcomes."""
    if is_attack:
        return "attack"
    if approach and approach.lower() in SOCIAL_SKILLS:
        return "social"
    return "any"


@dataclass(frozen=True)
class GateDecision:
    """The pre-roll gate's verdict on a check vs a bearer with N conditions."""

    outcome: CheckOutcome
    advantage: bool = False
    disadvantage: bool = False
    # The condition ids that justified the verdict (for logging + narration hints).
    triggering: tuple[str, ...] = ()
    # A composed narrator hint, e.g. "目標催眠：social 自動成功"; empty when ROLL.
    note: str = ""

    @property
    def short_circuits(self) -> bool:
        return self.outcome is not CheckOutcome.ROLL


# Priority among short-circuit outcomes when several conditions disagree.
# Higher index = stronger. AUTO_FAIL beats AUTO_SUCCESS so a dead-but-charmed
# target still can't be persuaded.
_OUTCOME_RANK = {
    CheckOutcome.ROLL: 0,
    CheckOutcome.AUTO_CRIT: 1,
    CheckOutcome.AUTO_SUCCESS: 2,
    CheckOutcome.AUTO_FAIL: 3,
}


def evaluate_gate(
    conditions: list[str] | set[str] | None,
    *,
    approach: str | None,
    is_attack: bool = False,
    condition_meta: dict[str, dict] | None = None,  # reserved for future leveled targets
) -> GateDecision:
    """Combine the bearer's conditions into a single pre-roll decision.

    - For outcomes: take the *strongest* short-circuit (rank table above). Ties
      keep the first.
    - For adv/disadv: collect both; resolution cancels them as in 5e.
    """
    if not conditions:
        return GateDecision(CheckOutcome.ROLL)

    cat = _category_for(approach, is_attack)
    skill_key = approach.lower() if approach else None
    chosen_outcome = CheckOutcome.ROLL
    advantage = False
    disadvantage = False
    triggering: list[str] = []
    notes: list[str] = []

    def _lookup(table: dict[str, "CheckOutcome | str"]):
        # Priority: exact skill name (e.g. "intimidation") → category ("social" /
        # "attack") → "any". This lets CHARMED single out intimidation while
        # still using SOCIAL_SKILLS for the broader hypnotized rule.
        for key in (skill_key, cat, "any"):
            if key and key in table:
                return table[key]
        return None

    for cid in conditions:
        eff = CATALOG.get(cid)
        if eff is None:
            continue
        candidate = _lookup(eff.outcomes) or CheckOutcome.ROLL
        if _OUTCOME_RANK[candidate] > _OUTCOME_RANK[chosen_outcome]:
            chosen_outcome = candidate
            triggering = [cid]
            notes = [f"{eff.label_zh}：{eff.description_zh}"]
        elif candidate is chosen_outcome and candidate is not CheckOutcome.ROLL:
            triggering.append(cid)
            notes.append(f"{eff.label_zh}：{eff.description_zh}")

        tag = _lookup(eff.advantage_against)
        if tag == "advantage":
            advantage = True
        elif tag == "disadvantage":
            disadvantage = True

    return GateDecision(
        outcome=chosen_outcome,
        advantage=advantage,
        disadvantage=disadvantage,
        triggering=tuple(triggering),
        note=" / ".join(notes),
    )


@dataclass(frozen=True)
class ActorEffect:
    """Aggregated actor-side modifier produced by `evaluate_actor`.

    `bonus_dice` is a signed d4-sized modifier (one die per active source, capped at
    one for the MVP); `advantage` / `disadvantage` cancel as in 5e; `band_downgrade`
    drops the result one degree of success (e.g. SUCCESS→FAILURE); `outcome`
    short-circuits the roll entirely
    (used by EXHAUSTED L6 = dead → AUTO_FAIL on anything).
    """

    outcome: CheckOutcome = CheckOutcome.ROLL
    bonus_dice: int = 0   # signed: +4 = +1d4, -4 = -1d4
    advantage: bool = False
    disadvantage: bool = False
    band_downgrade: bool = False
    triggering: tuple[str, ...] = ()
    note: str = ""

    @property
    def short_circuits(self) -> bool:
        return self.outcome is not CheckOutcome.ROLL


def evaluate_actor(
    conditions: list[str] | set[str] | None,
    *,
    approach: str | None,
    is_attack: bool = False,
    condition_meta: dict[str, dict] | None = None,
) -> ActorEffect:
    """Combine the actor's own conditions into a single ActorEffect.

    Reads `condition_meta[id]` for parametric data (e.g. EXHAUSTED level). Picks one
    +/-1d4 source (bless wins over guidance; bane stacks negatively but doesn't double).
    """
    if not conditions:
        return ActorEffect()
    cat = _category_for(approach, is_attack)
    skill_key = approach.lower() if approach else None
    meta = condition_meta or {}

    bonus = 0
    advantage = False
    disadvantage = False
    band_downgrade = False
    outcome = CheckOutcome.ROLL
    triggering: list[str] = []
    notes: list[str] = []

    def _lookup(table: dict[str, object]):
        for key in (skill_key, cat, "any"):
            if key and key in table:
                return table[key]
        return None

    for cid in conditions:
        base, _ref = parse_parametric(cid)
        eff = CATALOG.get(base)
        if eff is None:
            continue
        applied = False

        # Exhausted is the one leveled flag we honour mechanically (5e ladder).
        if base == EXHAUSTED:
            level = int((meta.get(cid) or {}).get("level", 1))
            if level >= 6:
                outcome = CheckOutcome.AUTO_FAIL
                notes.append(f"{eff.label_zh} L6：死亡")
                triggering.append(cid)
                continue
            if level >= 3:
                disadvantage = True
                applied = True
            if level >= 1:
                notes.append(f"{eff.label_zh} L{level}")
            if applied:
                triggering.append(cid)
            continue

        if eff.actor_bonus_dice and bonus == 0:
            # Stack-safe: first source wins to avoid bless+guidance double-dipping.
            bonus = eff.actor_bonus_dice
            applied = True

        tag = _lookup(eff.actor_advantage)
        if tag == "advantage":
            advantage = True
            applied = True
        elif tag == "disadvantage":
            disadvantage = True
            applied = True

        cand = _lookup(eff.actor_outcomes)
        if isinstance(cand, CheckOutcome) and _OUTCOME_RANK[cand] > _OUTCOME_RANK[outcome]:
            outcome = cand
            applied = True

        if eff.band_downgrade:
            band_downgrade = True
            applied = True

        if applied:
            triggering.append(cid)
            notes.append(f"{eff.label_zh}：{eff.description_zh}")

    if advantage and disadvantage:
        advantage = disadvantage = False

    return ActorEffect(
        outcome=outcome,
        bonus_dice=bonus,
        advantage=advantage,
        disadvantage=disadvantage,
        band_downgrade=band_downgrade,
        triggering=tuple(triggering),
        note=" / ".join(notes),
    )


def label(condition_id: str) -> str:
    """Traditional-Chinese label for prompts/embeds; falls back to the raw id.

    Handles parametric ids: 'loyal_to:ent_perrin' → '效忠(ent_perrin)'."""
    base, ref = parse_parametric(condition_id)
    eff = CATALOG.get(base)
    if not eff:
        return condition_id
    return f"{eff.label_zh}({ref})" if ref else eff.label_zh


def known(condition_id: str) -> bool:
    base, _ = parse_parametric(condition_id)
    return base in CATALOG
