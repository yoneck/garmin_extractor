"""Sleep extraction tests using a fake client (no network)."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gar.extraction.sleep import extract_sleep
from gar.utils.storage import CsvStorage


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_sleep_data(self, date_str):
        self.calls.append(date_str)
        return self.payload


def _payload():
    start = int(datetime(2026, 11, 14, 23, 0, tzinfo=timezone.utc).timestamp())
    return {
        "dailySleepDTO": {
            "id": 123,
            "calendarDate": "2026-11-15",
            "sleepStartTimestampGMT": (start + 600) * 1000,
            "sleepEndTimestampGMT": (start + 8 * 3600) * 1000,
            "sleepTimeSeconds": 7 * 3600,
            "napTimeSeconds": 900,
            "deepSleepSeconds": 3600,
            "lightSleepSeconds": 4 * 3600,
            "remSleepSeconds": 2 * 3600,
            "awakeSleepSeconds": 1800,
            "avgHeartRate": 61,
            "avgSleepHRV": None,
            "averageSpO2Value": 97,
            "lowestSpO2Value": 93,
            "averageRespirationValue": 15,
            "avgSleepStress": 22,
            "awakeCount": 2,
            "sleepScores": {
                "overall": {"value": 78, "qualifierKey": "GOOD"},
                "deepPercentage": {"value": 24, "qualifierKey": "EXCELLENT"},
                "remPercentage": {"value": 19, "qualifierKey": "FAIR"},
                "lightPercentage": {"value": 57, "qualifierKey": "FAIR"},
                "awakeCount": {"value": 2, "qualifierKey": "FAIR"},
                "restlessness": {"value": 4, "qualifierKey": "FAIR"},
                "stress": {"value": 22, "qualifierKey": "GOOD"},
            },
            "sleepNeed": {"baseline": 470, "actual": 540, "feedback": "HIGHLY_INCREASED"},
            "nextSleepNeed": {"actual": 500},
            "sleepScoreFeedback": "POSITIVE",
            "sleepScoreInsight": "NONE",
            "dailyNapDTOS": [
                {"napTimeSec": 900, "calendarDate": "2026-11-15"}
            ],
        },
        "avgOvernightHrv": 42.0,
        "hrvStatus": "BALANCED",
        "restlessMomentsCount": 11,
        "avgSkinTempDeviationC": 0.2,
        "sleepLevels": [
            {"startGMT": "2026-11-14T23:10:00.0", "endGMT": "2026-11-15T00:10:00.0", "activityLevel": 0},
            {"startGMT": "2026-11-15T00:10:00.0", "endGMT": "2026-11-15T04:10:00.0", "activityLevel": 1},
            {"startGMT": "2026-11-15T04:10:00.0", "endGMT": "2026-11-15T06:10:00.0", "activityLevel": 2},
            {"startGMT": "2026-11-15T06:10:00.0", "endGMT": "2026-11-15T06:40:00.0", "activityLevel": 3},
        ],
        "sleepHeartRate": [
            {"value": 60, "startGMT": 1794698100000},
            {"value": 62, "startGMT": 1794698160000},
        ],
        "sleepStress": [{"value": 20, "startGMT": 1794698100000}],
        "hrvData": [{"value": 40.0, "startGMT": 1794698462000}],
        "wellnessEpochSPO2DataDTOList": [
            {"epochTimestamp": "2026-11-14T23:18:00.0", "spo2Reading": 96, "readingConfidence": 3}
        ],
        "sleepMovement": [{"startGMT": "2026-11-14T23:18:00.0", "activityLevel": 2.5}],
    }


def test_sleep_fields_stages_and_samples(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    client = FakeClient(_payload())
    extract_sleep(
        client,
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )

    summary = pd.read_csv(next((tmp_path / "sleep_summary").glob("*.csv")))
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["calendarDate"] == "2026-11-15"
    assert row["overallSleepScore"] == 78
    assert row["deepPct"] == 24
    assert row["remPct"] == 19
    assert row["avgHeartRate"] == 61
    assert row["avgSleepHRV"] == 42.0
    assert row["avgSpO2"] == 97
    assert row["hrvStatus"] == "BALANCED"
    assert row["sleepNeedActualMinutes"] == 540
    assert row["nextSleepNeedActualMinutes"] == 500
    assert row["napCount"] == 1

    stages = pd.read_csv(next((tmp_path / "sleep_stages").glob("*.csv")))
    assert len(stages) == 4
    assert set(stages["stage"]) == {"deep", "light", "rem", "awake"}
    assert stages["durationSeconds"].min() > 0

    samples = pd.read_csv(next((tmp_path / "sleep_samples").glob("*.csv")))
    assert set(samples["kind"]) == {"hr", "stress", "hrv", "spo2", "movement"}
    assert len(samples) == 6


def test_sleep_does_not_request_future_days(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    client = FakeClient({})
    extract_sleep(
        client,
        storage,
        datetime(2026, 11, 16, tzinfo=timezone.utc),
        datetime(2026, 11, 25, tzinfo=timezone.utc),
    )
    assert len(client.calls) == 1
    assert client.calls[0] <= datetime.now().strftime("%Y-%m-%d")
