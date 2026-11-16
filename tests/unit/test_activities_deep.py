"""Deep activity extraction tests."""
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from gar.extraction.activities_deep import extract_activity_depth
from gar.utils.storage import CsvStorage


class DepthClient:
    ActivityDownloadFormat = SimpleNamespace(ORIGINAL="orig", TCX="tcx", GPX="gpx")

    def __init__(self):
        self.ftp_calls = 0

    def get_cycling_ftp(self):
        self.ftp_calls += 1
        return {"functionalThresholdPower": 250}

    def get_activity_splits(self, activity_id):
        return {
            "lapDTOs": [
                {
                    "lapIndex": 1,
                    "distance": 1000,
                    "duration": 600,
                    "averageMovingSpeed": 1.66,
                    "averagePower": 200,
                    "normalizedPower": 240,
                    "averageHR": 140,
                },
                {
                    "lapIndex": 2,
                    "distance": 1000,
                    "duration": 620,
                    "averageMovingSpeed": 1.61,
                    "averagePower": 210,
                    "normalizedPower": 250,
                    "averageHR": 145,
                },
            ]
        }

    def get_activity_hr_in_timezones(self, activity_id):
        raise RuntimeError("hr endpoint down")

    def get_activity_power_in_timezones(self, activity_id):
        return [
            {"zoneNumber": 1, "secsInZone": 60, "zoneLowBoundary": 100},
            {"zoneNumber": 2, "secsInZone": 90, "zoneLowBoundary": 180},
            {"zoneNumber": 3, "secsInZone": 30, "zoneLowBoundary": 240},
        ]

    def download_activity(self, activity_id, dl_fmt=None):
        return b"zip-bytes"


def _read(tmp_path: Path, table: str) -> pd.DataFrame:
    return pd.read_csv(tmp_path / table / f"{table}.csv")


def test_depth_does_not_crash_when_hr_fails(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    client = DepthClient()
    extract_activity_depth(
        client,
        storage,
        tmp_path,
        {"activityId": "123"},
        download_fit=True,
    )
    laps = _read(tmp_path, "activity_laps")
    power = _read(tmp_path, "activity_powers")
    metrics = _read(tmp_path, "activity_power_metrics")
    assert len(laps) == 2
    assert len(power) == 3
    assert len(metrics) == 2
    assert metrics.iloc[0]["intensityFactor"] == 0.96
    assert metrics.iloc[0]["tss"] > 0
    assert (tmp_path / "fit" / "123.zip").read_bytes() == b"zip-bytes"
    assert client.ftp_calls == 1


def test_depth_ftp_cached_across_activities(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    client = DepthClient()
    extract_activity_depth(client, storage, tmp_path, {"activityId": "1"})
    extract_activity_depth(client, storage, tmp_path, {"activityId": "2"})
    assert client.ftp_calls == 1
