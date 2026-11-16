"""Tests for consolidated full_context building."""
import json
from datetime import datetime, timezone
from pathlib import Path

from gar.context.build_full_context import build
from gar.utils.storage import CsvStorage


def _ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def test_details_are_lists_and_meta_counts_are_correct(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    storage.sync(
        "activities",
        [
            {"activityId": "1", "timestamp": _ts(), "sportType": "CYCLING"},
            {"activityId": "2", "timestamp": _ts() - 86400, "sportType": "RUNNING"},
        ],
    )
    storage.sync_id(
        "activity_laps",
        [
            {"activityId": "1", "lapIndex": 1, "distance_m": 1000},
            {"activityId": "1", "lapIndex": 2, "distance_m": 1000},
            {"activityId": "1", "lapIndex": 3, "distance_m": 1000},
        ],
    )
    storage.sync_id(
        "activity_hr",
        [{"activityId": "1", "zone": z, "secondsInZone": 60} for z in range(1, 6)],
    )
    storage.sync("sleep_summary", [{"timestamp": _ts(), "sleepTimeSeconds": 25200}])

    out, count, meta = build(tmp_path)
    assert count == 2
    assert meta["activity_count"] == 2
    assert meta["biometric_count"] == 1
    assert meta["total_records"] == 3
    assert meta["activity_detail_counts"]["laps"] == 3
    assert meta["activity_detail_counts"]["hr_zones"] == 5
    assert meta["context_hash"]

    records = [json.loads(line) for line in out.read_text().splitlines()]
    activity = next(r for r in records if r["type"] == "activity" and r["activityId"] == 1)
    assert isinstance(activity["details"]["laps"], list)
    assert len(activity["details"]["laps"]) == 3
    assert len(activity["details"]["hr_zones"]) == 5
