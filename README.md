# AI Living World — 極簡化 MVP

A radically simplified MVP distilled from [`AI_Living_World_design_v1.0.md`](AI_Living_World_design_v1.0.md).
It keeps the design's **core principle** — *the program owns all dice/state/judgment; the
AI only parses intent and narrates, never touching a number* (§4.0) — but throws away the
living-world simulation, database, factions, and custom ranks to validate three things fast:

1. **AI GM 可行性** — can an AI run a faithful D&D session without fudging the math?
2. **前端操作性質** — do the Discord interactions (A/B/C method buttons, 🎲 dice button) and
   the web dashboard feel good?
3. **新手 onboarding** — can a TRPG newbie jump in with zero prep?

### What this MVP is
- **Standard D&D 5e** — d20 ability/skill checks vs DC, and a full turn-based combat loop
  (initiative, action economy, attack vs AC, damage, saving throws, death saves).
- **Two pre-made level-3 PCs** (a Fighter and a Cleric) — no character creation.
- **One short scenario** — *The Dawnbridge Caravan*, a ~30–45 min, 4-scene one-shot.
- **Discord bot** (play) **+ read-only web dashboard** (spectate).
- **No database** — a single in-memory `GameState`, snapshotted to `save/session.json`.

---

## Architecture (single process, no DB)

Because there is no database to share state through, the Discord bot and the FastAPI
dashboard run in **one Python process on one asyncio loop**, sharing the same in-memory
`GameState` object (design §5 collapsed to a monolith for the MVP).

```
Discord NL → [AI] intent parse (cheap model) → tier A/B/C
   A → engine resolves → 🎲 button → server rolls → reveal → [AI] narrate (strong model)
   B → method buttons → player picks → (A)
   C → clarifying buttons → player picks → (A)
        ↓ every result appended to the in-memory event_log
   Discord embeds  ←→  shared GameState  ←→  Web dashboard (SSE live updates)
```

| Layer | Module |
|---|---|
| Resolution Engine (truth source) | [`app/engine/`](app/engine) — `dice.py`, `rules_5e.py`, `combat.py`, `resolution.py`, `types.py` |
| AI Orchestrator (OpenRouter) | [`app/ai/`](app/ai) — `orchestrator.py`, `prompts.py`, `schemas.py` |
| State (in-memory + JSON snapshot) | [`app/state/game_state.py`](app/state/game_state.py) |
| Content (PCs / monsters / scenario) | [`app/content/`](app/content) |
| Discord front-end | [`app/discord_bot/`](app/discord_bot) — `bot.py`, `views.py`, `embeds.py` |
| Web dashboard | [`app/web/`](app/web) + `static/` |
| Entrypoint | [`app/run.py`](app/run.py) |

The AI **never touches numbers**: intent output is validated against a schema (DC proposals
are snapped to 5e anchors), and narration only dramatizes an already-computed
`ResolutionResult`. This is enforced by a guard test in `tests/test_ai.py`.

---

## Setup

### 1. Prerequisites
- **Python 3.11+** (developed on 3.14).
- A **Discord bot token** — https://discord.com/developers/applications
- An **OpenRouter API key** — https://openrouter.ai/keys (optional: it runs in an offline
  fallback mode without one, with canned narration).

### 2. Install
```powershell
python -m pip install -e .            # or: python -m pip install -e ".[dev]" for tests
```

### 3. Configure
Copy the example env file and fill it in:
```powershell
Copy-Item .env.example .env
```
Edit `.env`:
```
DISCORD_TOKEN=...                     # required to run the bot
DISCORD_GUILD_ID=...                  # optional: instant slash-command sync to one server
OPENROUTER_API_KEY=sk-or-...          # omit to run AI in offline/fallback mode
MODEL_INTENT=openai/gpt-4o-mini       # cheap model: intent parsing (§8.2)
MODEL_NARRATE=anthropic/claude-3.5-sonnet  # strong model: narration
```

### 4. Discord application setup
1. Create an application → **Bot** → copy the token into `DISCORD_TOKEN`.
2. Under **Bot → Privileged Gateway Intents**, enable **MESSAGE CONTENT INTENT**
   (required for natural-language play).
3. Invite the bot with the **`bot`** and **`applications.commands`** scopes and permissions
   to read/send messages and use embeds/buttons in your test channel.

---

## Run
```powershell
python -m app.run
```
- Discord bot connects, and the dashboard serves at **http://127.0.0.1:8000**.
- Without `DISCORD_TOKEN`, only the dashboard runs (useful for previewing the UI).

### Play (in your Discord channel)
1. `/start` — begins the adventure and shows the two heroes.
2. Each of the two players clicks **Play Bram** / **Play Lyra** (or `/join bram` / `/join lyra`).
3. Once both have joined, the first scene opens. **Just type what you do**, e.g.
   *“I buy Old Perrin a drink and ask where the caravan was headed.”*
4. When a check is needed, click the **🎲** button to roll.
5. Useful slash commands: `/character`, `/scene`, `/roll 1d20+3`, `/next`, `/fight`, `/help`.

Open the dashboard in a browser alongside Discord to watch character HP, the initiative
tracker, and the adventure log update live.

---

## Verify without Discord

**Offline end-to-end smoke** (runs the whole pipeline with canned inputs, no Discord, no
network needed):
```powershell
python -m scripts.smoke
```

**Tests** (deterministic engine, combat, resolution, the “AI never touches numbers” guard,
and the dashboard API):
```powershell
python -m pytest -q
```

---

## Known MVP simplifications
- **Single session**, bound to one channel; restart resumes from `save/session.json`.
- AI context = the last N event-log entries (no RAG / vector memory) — fine for a short
  one-shot.
- Combat abstracts positioning/movement and omits reactions & opportunity attacks; monster
  AI picks a random living target.
- “Full 5e combat” content covers only what the two pre-made PCs and this scenario need
  (not the entire PHB).

## Tuning knobs
See `.env` and `app/config.py`: `DICE_SEED` (deterministic dice), `NARRATE_CONTEXT_WINDOW`,
`AI_OFFLINE`, model IDs, and the web host/port. DC anchors live in `app/engine/rules_5e.py`;
the scenario and stat blocks live in `app/content/`.
