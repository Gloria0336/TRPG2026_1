"""The short one-shot: "The Dawnbridge Caravan".

A ~30–45 min, 4-scene adventure for two PCs designed to onboard TRPG newbies:
1. a social hook, 2. an exploration/skill challenge, 3. a combat, 4. a branching climax.
Scene data is plain dicts; the GM/bot advances scenes and the AI narrates from `summary`.
"""
from __future__ import annotations

# Title shown by /start.
TITLE = "The Dawnbridge Caravan"

INTRO = (
    "**The Dawnbridge Caravan** — a short adventure for two.\n\n"
    "The frontier village of **Dawnbridge** sits where the East Road crosses a rushing river. "
    "Three days ago, a merchant caravan set out east and never reached the next town. "
    "Tonight, in the warm light of the Gilded Tankard tavern, someone is about to ask for help."
)

HOW_TO_PLAY = (
    "**How to play** — just say what your character does, in plain language, in this channel.\n"
    "• *“I ask the bartender about the missing caravan.”* → I'll work out which skill check that is.\n"
    "• If a check is needed, I'll post a 🎲 button — click it to roll. The dice are rolled on the "
    "server; the button just reveals the result.\n"
    "• Not sure what to do? Say something vague and I'll offer you options.\n"
    "• Slash commands: `/character` (your sheet), `/scene` (where you are), `/roll 1d20+3` (manual roll)."
)

# ── Scenes ──────────────────────────────────────────────────────────────────
SCENES: list[dict] = [
    {
        "id": "tavern",
        "title": "Scene 1 — The Gilded Tankard",
        "summary": (
            "The party sits in the crowded Gilded Tankard tavern in Dawnbridge. Old Perrin, a "
            "worried merchant, approaches: his caravan vanished on the East Road three days ago "
            "and the militia won't search. He'll pay 50 gold to whoever finds it. A nervous, "
            "hooded patron in the corner keeps glancing at Perrin."
        ),
        "npcs": ["Old Perrin (merchant)", "a nervous hooded patron"],
        "challenges": {
            "persuasion": 13,    # haggle Perrin up / get more detail
            "insight": 12,       # read whether Perrin is hiding something
            "perception": 15,    # notice the hooded patron eavesdropping
            "intimidation": 15,  # lean on the hooded patron
        },
        "onboarding": [
            "“I buy Old Perrin a drink and ask exactly where the caravan was headed.” (persuasion/insight)",
            "“I glance around the room — is anyone listening in?” (perception)",
            "“I accept the job.” (advances the story)",
        ],
        "encounter": None,
        "advance_hint": "Once the party accepts the job and gathers what they can, move to the East Road.",
    },
    {
        "id": "east_road",
        "title": "Scene 2 — The East Road",
        "summary": (
            "Morning mist clings to the forest where the East Road narrows. A few hundred paces "
            "in, the party finds the caravan: two overturned wagons, scattered crates, dark "
            "bloodstains — but few bodies. Crude tracks lead off the road toward a rocky hillside. "
            "A tripwire glints across the path ahead."
        ),
        "npcs": [],
        "challenges": {
            "investigation": 12,  # search the wreck for clues
            "survival": 13,       # track where the attackers dragged captives
            "perception": 13,     # spot the tripwire / ambush sign
            "acrobatics": 12,     # if someone trips the wire (DEX)
        },
        "onboarding": [
            "“I search the wrecked wagons for clues.” (investigation)",
            "“I look for tracks leading away from the road.” (survival)",
            "“I scan the path ahead for traps.” (perception)",
        ],
        "encounter": None,
        "advance_hint": "Following the tracks leads to the goblin warren's mouth — and an ambush.",
    },
    {
        "id": "ambush",
        "title": "Scene 3 — Ambush at the Warren",
        "summary": (
            "The tracks end at a cleft in the hillside reeking of woodsmoke and goblin. As the "
            "party approaches, three goblins burst from the brush with rusty scimitars and a "
            "shrieking war-cry. Roll for initiative!"
        ),
        "npcs": [],
        "challenges": {},
        "onboarding": [
            "On your turn, say e.g. “I attack a goblin with my Longsword.”",
            "Lyra can cast Sacred Flame or Guiding Bolt, or heal with Cure Wounds.",
            "I'll post a 🎲 button for each attack.",
        ],
        "encounter": [("goblin", 3)],
        "advance_hint": "With the ambushers down, the party can press into the warren after the boss.",
    },
    {
        "id": "warren",
        "title": "Scene 4 — The Warren (Climax)",
        "summary": (
            "Inside the smoky warren, Grix the Goblin Boss holds a captured caravan guard at "
            "knifepoint, a lone goblin lieutenant at his side. Grix sneers: “Leave now, or the "
            "hostage dies!” The party can fight, talk Grix down, or try to slip the hostage free."
        ),
        "npcs": ["Grix the Goblin Boss", "a captured caravan guard"],
        "challenges": {
            # Non-combat resolutions of the climax:
            "persuasion": 15,    # talk Grix into a deal / surrender
            "intimidation": 15,  # cow the goblins into fleeing
            "stealth": 15,       # slip around and free the hostage
            "deception": 15,     # bluff a bigger force is coming
        },
        "onboarding": [
            "Fight: “I charge Grix!” → starts combat against the boss and his lieutenant.",
            "Talk: “I try to convince Grix to take the gold and let the guard go.” (persuasion)",
            "Sneak: “I slip into the shadows to free the hostage.” (stealth)",
        ],
        "encounter": [("goblin_boss", 1), ("goblin", 1)],  # used only if the party chooses to fight
        "advance_hint": "However it ends, the fate of the caravan and the hostage is now written.",
    },
]

ENDINGS: dict[str, str] = {
    "victory": (
        "With the goblins beaten and the hostage freed, the party leads the survivors back to "
        "Dawnbridge. Old Perrin weeps with relief and pays the promised gold — plus a little extra. "
        "Songs of the Dawnbridge rescue will be sung in the Gilded Tankard for weeks. **The End.**"
    ),
    "peaceful": (
        "Without a single killing blow, the party walks the freed hostage out of the warren. "
        "Word spreads that the new adventurers are as clever as they are brave. Old Perrin pays in "
        "full, and even the goblins remember the day they were outwitted, not slaughtered. **The End.**"
    ),
    "defeat": (
        "The warren falls silent but for goblin laughter. The party's tale ends here in the dark — "
        "though in a living world, even the fallen are remembered. **The End.**"
    ),
}


def scene_by_id(scene_id: str) -> dict | None:
    return next((s for s in SCENES if s["id"] == scene_id), None)


def scene_index(scene_id: str) -> int:
    for i, s in enumerate(SCENES):
        if s["id"] == scene_id:
            return i
    return -1


def first_scene() -> dict:
    return SCENES[0]


def next_scene(scene_id: str) -> dict | None:
    i = scene_index(scene_id)
    if i < 0 or i + 1 >= len(SCENES):
        return None
    return SCENES[i + 1]
