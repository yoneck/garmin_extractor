"""Performance extraction tests using a fake Garmin client."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gar.extraction.performance import (
    extract_performance,
    extract_personal_records,
    extract_race_predictions,
    extract_scores,
    extract_thresholds,
    extract_zone_definitions,
)
from gar.utils.storage import CsvStorage


class PerfClient:
    def get_cycling_ftp(self):
        return {
            "calendarDate": "2026-09-15T18:30:47.0",
            "sport": "CYCLING",
            "functionalThresholdPower": 309,
            "isStale": False,
            "biometricSourceType": "CHANGE_LOG",
        }

    def get_lactate_threshold(self):
        return {
            "speed_and_heart_rate": {
                "calendarDate": "2026-11-15T21:01:49.998",
                "speed": 0.3138,
                "heartRate": 165,
                "heartRateCycling": 172,
            },
            "power": {
                "calendarDate": "2026-11-15T20:58:55.0",
                "sport": "RUNNING",
                "functionalThresholdPower": 385,
                "powerToWeight": 4.43,
                "isStale": False,
            },
        }

    def get_power_zones(self):
        return [
            {
                "sport": "CYCLING",
                "functionalThresholdPower": 309.0,
                "zone1Floor": 0.0,
                "zone2Floor": 175.0,
                "zone3Floor": 234.0,
                "zone4Floor": 284.0,
                "zone5Floor": 323.0,
                "zone6Floor": 371.0,
                "zone7Floor": 474.0,
            }
        ]

    def get_heart_rate_zones(self):
        return [
            {
                "trainingMethod": "HR_MAX",
                "lactateThresholdHeartRateUsed": 165,
                "zone1Floor": 93,
                "zone2Floor": 113,
                "zone3Floor": 132,
                "zone4Floor": 153,
                "zone5Floor": 168,
                "maxHeartRateUsed": 189,
                "sport": "DEFAULT",
            }
        ]

    def get_personal_record(self):
        return [
            {
                "id": 3079192838,
                "typeId": 1,
                "status": "ACCEPTED",
                "activityId": 25091109813,
                "activityName": "Paris Bieganie",
                "activityType": "running",
                "value": 379.549,
                "prStartTimeGmt": 1794772041000,
                "activityStartDateTimeInGMT": 1794771363000,
            },
            {"id": None, "value": 1},
        ]

    def get_race_predictions(self, *args):
        return {
            "calendarDate": "2026-11-16",
            "time5K": 1621,
            "time10K": 3555,
            "timeHalfMarathon": 8264,
            "timeMarathon": 19017,
        }

    def get_fitnessage_data(self, when):
        return {
            "chronologicalAge": 46,
            "fitnessAge": 43.96,
            "achievableFitnessAge": 39.79,
            "lastUpdated": "2026-11-16T00:00:00.0",
        }

    def get_endurance_score(self, start, end):
        return {
            "avg": 6707,
            "max": 7370,
            "groupMap": {
                "2026-11-10": {"groupAverage": 7309, "groupMax": 7370},
                "2026-11-03": {"groupAverage": 7291, "groupMax": 7323},
            },
        }

    def get_hill_score(self, start, end):
        return {
            "maxScore": 38,
            "hillScoreDTOList": [
                {
                    "calendarDate": "2026-11-16",
                    "strengthScore": 18,
                    "enduranceScore": 21,
                    "hillScoreClassificationId": 2,
                    "overallScore": 38,
                    "deviceId": 3447441416,
                },
                {
                    "calendarDate": "2026-11-15",
                    "strengthScore": 18,
                    "enduranceScore": 21,
                    "overallScore": 38,
                    "deviceId": 3447441416,
                },
            ],
        }


def _read(tmp_path: Path, table: str) -> pd.DataFrame:
    path = tmp_path / table / f"{table}.csv"
    files = [path] if path.exists() else sorted((tmp_path / table).glob("*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(p) for p in files], ignore_index=True)


def test_thresholds_include_ftp_and_lactate(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    assert extract_thresholds(PerfClient(), storage, "2026-11-16") == 4
    df = _read(tmp_path, "thresholds")
    assert set(df["kind"]) == {
        "ftp",
        "lactate_threshold_pace",
        "lactate_threshold_hr",
        "running_ftp",
    }
    ftp = df[df["kind"] == "ftp"].iloc[0]
    assert ftp["value"] == 309
    assert ftp["unit"] == "W"
    assert ftp["date"] == "2026-09-15"
    run_ftp = df[df["kind"] == "running_ftp"].iloc[0]
    assert run_ftp["value"] == 385
    assert run_ftp["valueSecondary"] == 4.43


def test_zone_definitions_upper_bounds(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    assert extract_zone_definitions(PerfClient(), storage, "2026-11-16") == 12
    df = _read(tmp_path, "zone_definitions")
    power = df[df["zoneType"] == "power"].sort_values("zone")
    assert len(power) == 7
    assert power.iloc[0]["lowerBound"] == 0
    assert power.iloc[0]["upperBound"] == 174
    assert pd.isna(power.iloc[-1]["upperBound"])
    hr = df[df["zoneType"] == "hr"].sort_values("zone")
    assert len(hr) == 5
    assert hr.iloc[0]["upperBound"] == 112
    assert hr.iloc[0]["lthr"] == 165


def test_personal_records_skip_missing_id(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    assert extract_personal_records(PerfClient(), storage) == 1
    df = _read(tmp_path, "personal_records")
    assert int(df.iloc[0]["recordId"]) == 3079192838
    assert df.iloc[0]["achievedAt"] > 0


def test_race_predictions_have_paces(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    assert extract_race_predictions(
        PerfClient(),
        storage,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 16, tzinfo=timezone.utc),
    ) == 4
    df = _read(tmp_path, "race_predictions").set_index("distance")
    assert df.loc["5K", "predictedSeconds"] == 1621
    assert df.loc["5K", "predictedPaceSecPerKm"] == 324.2
    assert round(df.loc["M", "predictedSpeedKmh"], 2) == 7.99


def test_scores_fitness_endurance_hill(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    count = extract_scores(
        PerfClient(),
        storage,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 16, tzinfo=timezone.utc),
    )
    assert count == 5
    df = _read(tmp_path, "scores")
    assert set(df["scoreType"]) == {"fitness_age", "endurance_score", "hill_score"}
    fitness = df[df["scoreType"] == "fitness_age"].iloc[0]
    assert fitness["value"] == 43.96
    assert fitness["valueSecondary"] == 39.79


def test_extract_performance_orchestrator(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    counts = extract_performance(
        PerfClient(),
        storage,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 16, tzinfo=timezone.utc),
        today=datetime(2026, 11, 16, tzinfo=timezone.utc),
    )
    assert counts["thresholds"] == 4
    assert counts["zone_definitions"] == 12
    assert counts["personal_records"] == 1
    assert counts["race_predictions"] == 4
    assert counts["scores"] == 5
