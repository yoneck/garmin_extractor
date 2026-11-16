"""Activity extraction tests."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gar.extraction.activities import extract_activities
from gar.utils.storage import CsvStorage


class ActivityClient:
    def get_activities_by_date(self, start, end):
        return [
            {
                "activityId": 25091109813,
                "beginTimestamp": 1789318758000,
                "activityType": {"typeKey": "road_biking"},
                "activityName": "Gravel Z2",
                "locationName": "Warszawa",
                "distance": 58400,
                "duration": 7500,
                "movingDuration": 7200,
                "elapsedDuration": 7800,
                "elevationGain": 232,
                "averageHR": 141,
                "maxHR": 172,
                "averageSpeed": 7.8,
                "maxSpeed": 12.0,
                "avgPower": 180,
                "normPower": 198,
                "maxPower": 520,
                "averageRunningCadenceInStepsPerMinute": None,
                "averageBikeCadence": 82,
                "calories": 720,
                "steps": 0,
                "differenceBodyBattery": -12,
                "aerobicTrainingEffect": 3.2,
                "anaerobicTrainingEffect": 0.1,
                "trainingEffectLabel": "IMPROVING",
                "activityTrainingLoad": 118,
                "vO2MaxValue": 47.0,
                "moderateIntensityMinutes": 60,
                "vigorousIntensityMinutes": 35,
                "hrTimeInZone_1": 600,
                "hrTimeInZone_2": 3600,
                "powerTimeInZone_1": 300,
                "powerTimeInZone_2": 4000,
            }
        ]


def test_activity_full_fields_and_zones(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    ids = extract_activities(
        ActivityClient(),
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    assert ids == ["25091109813"]
    df = pd.concat([pd.read_csv(p) for p in (tmp_path / "activities").glob("*.csv")])
    assert len(df) == 1
    row = df.iloc[0]
    assert row["trainingLoad"] == 118
    assert row["normPower"] == 198
    assert row["vo2max"] == 47.0
    assert row["differenceBodyBattery"] == -12

    zones = pd.read_csv(tmp_path / "activity_zones" / "activity_zones.csv")
    assert len(zones) == 4
    assert set(zones["zoneType"]) == {"hr", "power"}
