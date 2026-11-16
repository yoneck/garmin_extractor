"""Daily wellness extraction tests."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gar.extraction.daily import extract_daily
from gar.utils.storage import CsvStorage


class DailyClient:
    def get_user_summary(self, date_str):
        return {
            "totalSteps": 12000,
            "totalDistanceMeters": 9800,
            "restingHeartRate": 62,
            "averageStressLevel": 28,
            "maxStressLevel": 70,
            "bodyBatteryAtWakeTime": 71,
            "bodyBatteryChargedValue": 60,
            "bodyBatteryDrainedValue": 80,
            "bodyBatteryHighestValue": 80,
            "bodyBatteryLowestValue": 20,
            "averageSpo2": 96,
            "avgWakingRespirationValue": 15,
            "moderateIntensityMinutes": 30,
            "vigorousIntensityMinutes": 20,
            "sleepingSeconds": 25000,
        }

    def get_training_readiness(self, date_str):
        return [
            {
                "score": 62,
                "level": "MODERATE",
                "feedbackShort": "RECOVERY_NEEDED",
                "recoveryTime": 720,
                "hrvFactorPercent": 40,
                "sleepScoreFactorPercent": 60,
                "stressHistoryFactorPercent": 80,
                "acwrFactorPercent": 30,
                "sleepScore": 56,
                "hrvWeeklyAverage": 36,
                "validSleep": True,
            }
        ]

    def get_training_status(self, date_str):
        return {
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "3447441416": {
                        "trainingStatus": 8,
                        "trainingStatusFeedbackPhrase": "STRAINED_3",
                        "fitnessTrend": 0,
                        "fitnessTrendSport": "NONE",
                        "weeklyTrainingLoad": None,
                        "acuteTrainingLoadDTO": {
                            "dailyTrainingLoadAcute": 157,
                            "dailyTrainingLoadChronic": 219,
                            "acwrPercent": 29,
                            "acwrStatus": "LOW",
                        },
                    }
                }
            },
            "mostRecentTrainingLoadBalance": {
                "metricsTrainingLoadBalanceDTOMap": {
                    "3447441416": {
                        "trainingBalanceFeedbackPhrase": "BELOW_TARGETS",
                        "monthlyLoadAerobicLow": 153,
                        "monthlyLoadAerobicHigh": 61,
                        "monthlyLoadAnaerobic": 0,
                    }
                }
            },
            "mostRecentVO2Max": {
                "generic": {"vo2MaxValue": 47.0},
                "cycling": None,
            },
        }

    def get_rhr_day(self, date_str):
        return {
            "allMetrics": {
                "metricsMap": {
                    "WELLNESS_RESTING_HEART_RATE": [
                        {"value": 65.0, "calendarDate": date_str}
                    ]
                }
            }
        }

    def get_max_metrics(self, date_str):
        return [
            {
                "generic": {"vo2MaxValue": 47.0, "fitnessAge": 44},
                "cycling": {"vo2MaxValue": 52.0},
                "heatAltitudeAcclimation": {
                    "heatAcclimationPercentage": 12,
                    "altitudeAcclimation": 0,
                    "currentAltitude": 44,
                },
            }
        ]


def test_daily_tables(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    extract_daily(
        DailyClient(),
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    daily = pd.read_csv(tmp_path / "daily_summary" / "2026_11.csv")
    assert daily.iloc[0]["totalSteps"] == 12000
    assert daily.iloc[0]["readinessScore"] == 62
    assert daily.iloc[0]["trainingStatus"] == 8
    assert daily.iloc[0]["acuteLoad7d"] == 157
    assert daily.iloc[0]["vo2maxRunning"] == 47.0

    readiness = pd.read_csv(tmp_path / "readiness" / "2026_11.csv")
    assert readiness.iloc[0]["score"] == 62

    status = pd.read_csv(tmp_path / "training_status" / "2026_11.csv")
    assert status.iloc[0]["deviceId"] == "3447441416" or status.iloc[0]["deviceId"] == 3447441416
    assert status.iloc[0]["acwrStatus"] == "LOW"

    metrics = pd.read_csv(tmp_path / "max_metrics" / "2026_11.csv")
    assert metrics.iloc[0]["vo2maxCycling"] == 52.0
