"""Tests for sync watermark state."""
from datetime import datetime, timezone
from pathlib import Path

from gar.utils.state import SyncState


def test_update_and_read_watermark(tmp_path: Path):
    state = SyncState(tmp_path / "state.json")
    assert state.watermark("activities") is None
    when = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    state.update("activities", watermark=when, status="ok", rows=42)
    loaded = state.get("activities")
    assert loaded["status"] == "ok"
    assert loaded["rows"] == 42
    assert state.watermark("activities") == when


def test_interrupted_update_keeps_previous_watermark(tmp_path: Path):
    state = SyncState(tmp_path / "state.json")
    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    state.update("sleep", watermark=first, status="ok", rows=10)
    state.update("sleep", status="error", error="boom")
    assert state.watermark("sleep") == first


def test_corrupt_file_recovers(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = SyncState(path)
    assert state.load() == {"datasets": {}}
