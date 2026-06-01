"""Centralized structured logger for tracing one app run at a time.

Every step that touches a player action, AI interpret, HTTP call, JSON parse,
schema validation, intent dispatch, dice roll, or narration should log through
`get_logger(...)` so the current run's trace file tells the whole story.

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
import os
import sys
from pathlib import Path

from .config import ROOT_DIR

LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

TRACE_FILE_PREFIX = "trace"

_initialized = False
_closed = False
_trace_file: Path | None = None
_console_ctrl_handler: object | None = None


def _new_trace_file() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return LOG_DIR / f"{TRACE_FILE_PREFIX}_{stamp}_{os.getpid()}.log"


def current_trace_file() -> Path | None:
    """Return the per-run trace file after logging has been initialized."""
    return _trace_file


def _register_terminal_close_handler() -> None:
    """Close the log file when a Windows console window is closed."""
    global _console_ctrl_handler
    if os.name != "nt" or _console_ctrl_handler is not None:
        return

    try:
        import ctypes

        ctrl_close_event = 2
        ctrl_logoff_event = 5
        ctrl_shutdown_event = 6
        handled_events = {ctrl_close_event, ctrl_logoff_event, ctrl_shutdown_event}
        handler_routine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        def handler(ctrl_type: int) -> bool:
            if ctrl_type in handled_events:
                close_logging()
            return False

        _console_ctrl_handler = handler_routine(handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ctrl_handler, True)
    except Exception:
        return


def setup_logging(console_level: int = logging.INFO, file_level: int = logging.DEBUG) -> None:
    """Initialize the `trpg` logger tree. Idempotent and safe from multiple entrypoints."""
    global _initialized, _closed, _trace_file
    if _initialized:
        return

    _closed = False
    _trace_file = _new_trace_file()

    fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)-22s | %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = logging.FileHandler(_trace_file, mode="w", encoding="utf-8")
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
    root.info("logger initialized - trace file: %s", _trace_file)
    atexit.register(close_logging)
    _register_terminal_close_handler()


def close_logging() -> None:
    """Finalize the current run's log file and close all handlers."""
    global _initialized, _closed
    if not _initialized or _closed:
        return

    root = logging.getLogger("trpg")
    handlers = list(root.handlers)
    record = root.makeRecord(
        root.name,
        logging.INFO,
        __file__,
        0,
        "logger closing - finalized trace file: %s",
        (_trace_file,),
        None,
    )

    for handler in handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                handler.handle(record)
            except (OSError, ValueError):
                pass
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
