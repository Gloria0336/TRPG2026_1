import logging

from app import logging_setup


def test_recording_lifecycle_is_controlled_explicitly(tmp_path, monkeypatch):
    logging_setup.close_logging()
    monkeypatch.setattr(logging_setup, "LOG_DIR", tmp_path)

    logging_setup.setup_logging(console_level=logging.CRITICAL)
    assert logging_setup.current_trace_file() is None
    assert list(tmp_path.glob("trace_*.log")) == []

    path = logging_setup.start_recording(channel_id=123, actor="user-1")
    logging_setup.get_logger("test").info("campaign event")
    finalized = logging_setup.finish_recording("/finish by user-1")

    assert finalized == path
    assert logging_setup.current_trace_file() is None
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "recording started by /start" in text
    assert "campaign event" in text
    assert "recording finished - reason=/finish by user-1" in text

    logging_setup.close_logging()
