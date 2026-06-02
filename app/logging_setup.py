"""Centralized structured logger for tracing one campaign recording at a time.

Every step that touches a player action, AI interpret, HTTP call, JSON parse,
schema validation, intent dispatch, dice roll, or narration should log through
`get_logger(...)` so the active campaign recording tells the whole story.

The console logger is always available while the app runs. File recording is
started explicitly by `/start` and finished explicitly by `/finish`.

Use levels deliberately:
  DEBUG   payload contents (prompts, raw responses, parsed JSON, intent fields)
  INFO    step boundaries (entering / leaving a stage, branching decisions)
  WARNING recoverable fallback path taken (AI offline, schema mismatch, retry)
  ERROR   exception caught; always include exc_info=True so traceback lands in log
"""
from __future__ import annotations

import atexit
from datetime import datetime
import logging
import logging.handlers
import sys
from pathlib import Path

from .config import ROOT_DIR

LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

TRACE_PREFIX = "trace"
TRACE_MAX_BYTES = 4_000_000
TRACE_BACKUP_COUNT = 3

_initialized = False
_closed = False
_trace_file: Path | None = None
_file_handler: logging.handlers.RotatingFileHandler | None = None
_console_level = logging.INFO
_file_level = logging.DEBUG
_formatter = logging.Formatter(
    fmt="%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)


def current_trace_file() -> Path | None:
    """Return the active trace file path, or None when no campaign is recording."""
    return _trace_file


def setup_logging(console_level: int = logging.INFO, file_level: int = logging.DEBUG) -> None:
    """Initialize the `trpg` console logger. Idempotent and safe from multiple entrypoints."""
    global _initialized, _closed, _console_level, _file_level
    _console_level = console_level
    _file_level = file_level
    if _initialized:
        return

    _closed = False

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(_formatter)

    root = logging.getLogger("trpg")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.propagate = False

    # Quiet the noisier deps but keep their warnings.
    for noisy in ("httpx", "httpcore", "discord", "discord.client", "discord.gateway", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True
    root.info("logger initialized - file recording waits for /start")
    atexit.register(close_logging)


def _trace_filename(channel_id: int | None = None) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    channel = f"_ch{channel_id}" if channel_id else ""
    return f"{TRACE_PREFIX}_{stamp}{channel}.log"


def start_recording(channel_id: int | None = None, actor: str | None = None) -> Path:
    """Open a fresh campaign trace file and begin writing logs to it."""
    global _file_handler, _trace_file
    if not _initialized:
        setup_logging()
    if _file_handler is not None:
        finish_recording("replaced by a new /start")

    LOG_DIR.mkdir(exist_ok=True)
    _trace_file = LOG_DIR / _trace_filename(channel_id)
    handler = logging.handlers.RotatingFileHandler(
        _trace_file,
        maxBytes=TRACE_MAX_BYTES,
        backupCount=TRACE_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(_file_level)
    handler.setFormatter(_formatter)
    _file_handler = handler

    root = logging.getLogger("trpg")
    root.addHandler(handler)
    root.info("recording started by /start actor=%s channel=%s trace_file=%s", actor, channel_id, _trace_file)
    return _trace_file


def finish_recording(reason: str = "/finish") -> Path | None:
    """Finish the active campaign recording and close its file handler."""
    global _file_handler, _trace_file
    if _file_handler is None:
        return None

    root = logging.getLogger("trpg")
    finalized = _trace_file
    root.info("recording finished - reason=%s trace_file=%s", reason, finalized)

    handler = _file_handler
    try:
        handler.flush()
    except (OSError, ValueError):
        pass
    try:
        handler.close()
    except (OSError, ValueError):
        pass
    root.removeHandler(handler)
    _file_handler = None
    _trace_file = None
    return finalized


def close_logging() -> None:
    """Close logger resources during app shutdown."""
    global _initialized, _closed, _file_handler, _trace_file
    if not _initialized or _closed:
        return

    root = logging.getLogger("trpg")
    if _file_handler is not None:
        handler = _file_handler
        root.removeHandler(handler)
        try:
            handler.flush()
        except (OSError, ValueError):
            pass
        try:
            handler.close()
        except (OSError, ValueError):
            pass
        _file_handler = None
        _trace_file = None

    handlers = list(root.handlers)
    for handler in handlers:
        try:
            handler.flush()
        except (OSError, ValueError):
            pass
        try:
            handler.close()
        except (OSError, ValueError):
            pass
        root.removeHandler(handler)

    _closed = True
    _initialized = False
    _file_handler = None
    _trace_file = None


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
