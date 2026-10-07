"""Tests for storage de-duplication and context generation using fixtures only."""
import tempfile
from datetime import datetime
from pathlib import Path

from gar.utils.storage import CsvStorage


def _sample_activities():
    now = int(datetime.now().timestamp())
    return [
        {"activityId": "111", "timestamp": now, "sportType": "RUNNING", "distanceMeter": 1000},
        {"activityId": "111", "timestamp": now, "sportType": "RUNNING", "distanceMeter": 1000},
        {"activityId": "222", "timestamp": now, "sportType": "CYCLING", "distanceMeter": 5000},
    ]


def test_activity_dedup_keeps_unique_ids():
    with tempfile.TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        storage.sync("activities", _sample_activities())
        context = storage.last_period("activities")
        assert context is not None


def test_sync_id_writes_per_activity_table():
    with tempfile.TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        n = storage.sync_id("activity_laps", [
            {"activityId": "111", "lapStartTime": "2024-01-01T00:00:00"},
            {"activityId": "222", "lapStartTime": "2024-01-01T00:00:00"},
        ])
        assert n == 2
        assert (Path(tmp) / "activity_laps" / "activity_laps.csv").exists()


def test_context_generation_empty():
    with tempfile.TemporaryDirectory() as tmp:
        from gar.context.llm_context import generate_context
        ctx = generate_context(Path(tmp))
        assert ctx["empty"] is True


def test_context_generation_with_data():
    with tempfile.TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        records = [
            {"timestamp": int(datetime.now().timestamp()), "hrvMean": 42.1, "hrvStdDev": 5},
            {"timestamp": int(datetime.now().timestamp()) - 86400, "hrvMean": 40.0, "hrvStdDev": 5},
        ]
        storage.sync("hrv", records)
        from gar.context.llm_context import generate_context
        ctx = generate_context(Path(tmp))
        assert ctx["empty"] is False
        assert "HRV" in ctx["content"]
        assert "generated_at" in ctx
