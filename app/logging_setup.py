"""Centralized structured logger for tracing the full data flow.

Every step that touches a player action — Discord entry, AI interpret, HTTP call,
JSON parse, schema validation, intent dispatch, dice roll, narration — should log
through `get_logger(...)` so a single `logs/trace.log` file tells the whole story.

Use levels deliberately:
  DEBUG  — payload contents (prompts, raw responses, parsed JSON, intent fields)
  INFO   — step boundaries (entering / leaving a stage, branching decisions)
  WARNING— recoverable fallback path taken (AI offline, schema mismatch, retry)
  ERROR  — exception caught; always include exc_info=True so traceback lands in log
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .config import ROOT_DIR

LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRACE_FILE = LOG_DIR / "trace.log"

_initialized = False


def setup_logging(console_level: int = logging.INFO, file_level: int = logging.DEBUG) -> None:
    """Initialize the `trpg` logger tree. Idempotent — safe to call from multiple entrypoints."""
    global _initialized
    if _initialized:
        return

    fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)-22s | %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        TRACE_FILE, maxBytes=4_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(fmt)

    root = logging.getLogger("trpg")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.propagate = False

    # Quiet the noisier deps but keep their warnings.
    for noisy in ("httpx", "httpcore", "discord", "discord.client", "discord.gateway", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True
    root.info("logger initialized — trace file: %s", TRACE_FILE)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the `trpg` namespace. Sets up logging on first call."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(f"trpg.{name}")


def truncate(s: object, limit: int = 500) -> str:
    """Render any value as a single-line string, truncated for log readability."""
    text = repr(s) if not isinstance(s, str) else s
    text = text.replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[+{len(text) - limit} chars]"
