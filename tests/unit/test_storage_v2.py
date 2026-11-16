"""Storage key, atomicity and timestamp tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gar.utils.storage import CsvStorage


def _ts(days_ago: int = 0) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())


def test_composite_keys_keep_all_laps_and_zones():
    with __import__("tempfile").TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        laps = [
            {"activityId": "1", "lapIndex": 1, "distance_m": 1000},
            {"activityId": "1", "lapIndex": 2, "distance_m": 1000},
            {"activityId": "1", "lapIndex": 3, "distance_m": 1000},
        ]
        assert storage.sync_id("activity_laps", laps) == 3
        zones = [
            {"activityId": "1", "zone": z, "secondsInZone": 60}
            for z in range(1, 6)
        ]
        assert storage.sync_id("activity_hr", zones) == 5
        df = pd.read_csv(Path(tmp) / "activity_laps" / "activity_laps.csv")
        assert len(df) == 3
        df = pd.read_csv(Path(tmp) / "activity_hr" / "activity_hr.csv")
        assert len(df) == 5


def test_composite_sync_is_idempotent_and_updates_last():
    with __import__("tempfile").TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        storage.sync_id(
            "activity_laps",
            [{"activityId": "1", "lapIndex": 1, "distance_m": 100}],
        )
        storage.sync_id(
            "activity_laps",
            [{"activityId": "1", "lapIndex": 1, "distance_m": 999}],
        )
        df = pd.read_csv(Path(tmp) / "activity_laps" / "activity_laps.csv")
        assert len(df) == 1
        assert int(df.iloc[0]["distance_m"]) == 999


def test_rejects_zero_timestamp_and_quarantines():
    with __import__("tempfile").TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        storage.sync(
            "stress",
            [
                {"timestamp": 0, "averageStress": 10},
                {"timestamp": _ts(1), "averageStress": 20},
            ],
        )
        files = list((Path(tmp) / "stress").glob("*.csv"))
        df = pd.concat([pd.read_csv(p) for p in files]) if files else pd.DataFrame()
        assert len(df) == 1
        q = Path(tmp) / "quarantine" / "stress.jsonl"
        assert q.exists() and "invalid" in q.read_text()


def test_future_timestamp_rejected():
    with __import__("tempfile").TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        future = int((datetime.now(timezone.utc) + timedelta(days=10)).timestamp())
        storage.sync("stress", [{"timestamp": future, "averageStress": 10}])
        assert storage.latest_timestamp("stress") is None


def test_latest_timestamp_uses_max_not_min():
    with __import__("tempfile").TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        storage.sync(
            "activities",
            [
                {"activityId": "1", "timestamp": _ts(10)},
                {"activityId": "2", "timestamp": _ts(2)},
            ],
        )
        latest = storage.latest_timestamp("activities")
        assert latest is not None
        assert abs((datetime.now(timezone.utc) - latest).days - 2) <= 1


def test_activity_dedup_keeps_last():
    with __import__("tempfile").TemporaryDirectory() as tmp:
        storage = CsvStorage(Path(tmp))
        storage.sync(
            "activities",
            [
                {"activityId": "1", "timestamp": _ts(1), "sportType": "RUNNING"},
                {"activityId": "1", "timestamp": _ts(1), "sportType": "TRAIL_RUN"},
            ],
        )
        frames = [
            pd.read_csv(p) for p in (Path(tmp) / "activities").glob("*.csv")
        ]
        df = pd.concat(frames)
        assert len(df) == 1
        assert df.iloc[0]["sportType"] == "TRAIL_RUN"
