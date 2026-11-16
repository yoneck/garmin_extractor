"""Plan/gear/weather/strength tests with a fake client."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gar.extraction.plan import (
    extract_adherence,
    extract_gear,
    extract_plan,
    extract_strength_sets,
    extract_workouts,
    SPORT_ALIASES,
)
from gar.utils.storage import CsvStorage


class PlanClient:
    def get_workouts(self, start, limit):
        return [
            {
                "workoutId": 1693572420,
                "workoutName": "Interwały Z4",
                "sportType": {"sportTypeKey": "cycling"},
                "estimatedDurationInSecs": 4740,
                "estimatedDistanceInMeters": 34008.0,
                "createdDate": "2026-09-10T13:45:46.0",
                "updateDate": "2026-09-10T13:46:12.0",
            },
            {"workoutId": None, "workoutName": "broken"},
        ]

    def get_workout_by_id(self, workout_id):
        return {
            "workoutId": int(workout_id),
            "workoutSegments": [
                {
                    "segmentOrder": 1,
                    "workoutSteps": [
                        {
                            "type": "ExecutableStepDTO",
                            "stepOrder": 1,
                            "stepType": {"stepTypeKey": "warmup"},
                            "endCondition": {"conditionTypeKey": "time"},
                            "endConditionValue": 1200.0,
                            "targetType": {"workoutTargetTypeKey": "heart.rate.zone"},
                            "zoneNumber": 1,
                        },
                        {
                            "type": "RepeatGroupDTO",
                            "stepOrder": 2,
                            "stepType": {"stepTypeKey": "repeat"},
                            "numberOfIterations": 4,
                            "workoutSteps": [
                                {
                                    "type": "ExecutableStepDTO",
                                    "stepOrder": 3,
                                    "stepType": {"stepTypeKey": "interval"},
                                    "endCondition": {"conditionTypeKey": "time"},
                                    "endConditionValue": 480.0,
                                    "targetType": {"workoutTargetTypeKey": "power.zone"},
                                    "zoneNumber": 4,
                                }
                            ],
                        },
                    ],
                }
            ],
        }

    def get_activities_by_date(self, start, end):
        return [
            {
                "activityId": 111,
                "activityType": {"typeKey": "road_biking"},
                "startTimeLocal": "2026-11-10 10:00:00",
            }
        ]

    def get_scheduled_workouts(self, year, month):
        if month != 11:
            return {"calendarItems": []}
        return {
            "calendarItems": [
                {
                    "id": 1,
                    "itemType": "workout",
                    "date": "2026-11-10",
                    "title": "Interwały Z4",
                    "sportTypeKey": "cycling",
                    "workoutId": 1693572420,
                },
                {
                    "id": 2,
                    "itemType": "workout",
                    "date": "2026-11-12",
                    "title": "Baza",
                    "sportTypeKey": "cycling",
                },
                {
                    "id": 3,
                    "itemType": "fbtAdaptiveWorkout",
                    "date": "2026-11-30",
                    "title": "Baza",
                    "sportTypeKey": "cycling",
                },
                {"id": 4, "itemType": "weight", "date": "2026-11-01"},
            ]
        }

    def get_gear(self, pk):
        return [
            {
                "gearPk": 35758300,
                "uuid": "5e1066538c414ab6a940f9d246f294d3",
                "displayName": "Rondo Ruut CF2",
                "gearTypeName": "Bike",
                "gearStatusName": "active",
                "maximumMeters": 5000000.0,
                "dateBegin": "2023-07-09T00:00:00.0",
            }
        ]

    def get_gear_activities(self, gear_uuid, limit):
        return [
            {
                "activityId": 111,
                "activityName": "Gravel",
                "activityType": {"typeKey": "road_biking"},
                "startTimeLocal": "2026-11-10 10:00:00",
                "distance": 58400,
            }
        ]

    def get_activity_weather(self, activity_id):
        return {
            "issueDate": "2026-11-15T19:00:00.000+00:00",
            "temp": 52,
            "apparentTemp": 50,
            "dewPoint": 48,
            "relativeHumidity": 87,
            "windSpeed": 15,
            "windDirection": 210,
            "windDirectionCompassPoint": "ssw",
            "weatherStationDTO": {"id": "LFPB", "name": "Paris"},
            "weatherTypeDTO": {"desc": "Rain"},
            "latitude": 48.9,
            "longitude": 2.4,
        }

    def get_activity_exercise_sets(self, activity_id):
        return {
            "activityId": int(activity_id),
            "exerciseSets": [
                {
                    "exercises": [
                        {"category": "ROW", "name": None, "probability": 89.4},
                        {"category": "UNKNOWN", "name": None, "probability": 9.7},
                    ],
                    "duration": 87.8,
                    "repetitionCount": 9,
                    "weight": 0.0,
                    "setType": "ACTIVE",
                    "startTime": "2026-11-08T18:16:09.0",
                }
            ],
        }

    def get_user_profile(self):
        return {"id": 117462700}


def _read(tmp_path: Path, table: str) -> pd.DataFrame:
    path = tmp_path / table / f"{table}.csv"
    files = [path] if path.exists() else sorted((tmp_path / table).glob("*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(p) for p in files], ignore_index=True)


def test_workouts_and_nested_steps(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    assert extract_workouts(PlanClient(), storage) == 1
    workouts = _read(tmp_path, "workouts")
    assert workouts.iloc[0]["workoutName"] == "Interwały Z4"
    steps = _read(tmp_path, "workout_steps")
    assert len(steps) == 3
    repeat = steps[steps["fragmentType"] == "RepeatGroupDTO"].iloc[0]
    assert repeat["numberOfIterations"] == 4
    interval = steps[steps["stepType"] == "interval"].iloc[0]
    assert interval["parentStepOrder"] == 2
    assert interval["zoneNumber"] == 4


def test_adherence_statuses(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    count = extract_adherence(
        PlanClient(),
        storage,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 16, tzinfo=timezone.utc),
    )
    assert count == 3
    rows = _read(tmp_path, "workout_adherence").set_index("scheduledId")
    assert rows.loc[1, "status"] == "completed"
    assert int(rows.loc[1, "matchedActivityId"]) == 111
    assert rows.loc[2, "status"] == "missed"
    assert rows.loc[3, "status"] == "upcoming"


def test_gear_and_usage(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    assert extract_gear(PlanClient(), storage, 117462700) == 1
    gear = _read(tmp_path, "gear")
    assert gear.iloc[0]["name"] == "Rondo Ruut CF2"
    assert gear.iloc[0]["maxMeters"] == 5000000.0
    usage = _read(tmp_path, "gear_usage")
    assert int(usage.iloc[0]["activityId"]) == 111


def test_weather_units_and_strength_sets(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    counts = extract_plan(
        PlanClient(),
        storage,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 16, tzinfo=timezone.utc),
        activity_ids=["123"],
    )
    assert counts["weather"] == 1
    assert counts["strength_sets"] == 1
    weather = _read(tmp_path, "weather")
    assert weather.iloc[0]["tempC"] == 11.1
    assert weather.iloc[0]["windSpeedKmh"] == 24.1
    assert weather.iloc[0]["condition"] == "Rain"

    sets = _read(tmp_path, "strength_sets")
    assert sets.iloc[0]["exerciseCategory"] == "ROW"
    assert sets.iloc[0]["repetitions"] == 9
    assert sets.iloc[0]["timestamp"] > 0


def test_sport_aliases_cover_cycling_variants():
    assert "gravel_cycling" in SPORT_ALIASES["cycling"]
    assert "trail_running" in SPORT_ALIASES["running"]
