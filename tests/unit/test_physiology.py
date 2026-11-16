"""Physiology extraction tests with fake clients."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gar.extraction.physiology import (
    extract_body_battery,
    extract_body_composition,
    extract_hrv,
    extract_stress,
    parse_ts,
)
from gar.utils.storage import CsvStorage


def _read(tmp_path: Path, table: str) -> pd.DataFrame:
    files = list((tmp_path / table).glob("*.csv"))
    return pd.concat([pd.read_csv(p) for p in files], ignore_index=True) if files else pd.DataFrame()


def test_parse_ts_formats():
    assert parse_ts("2026-11-15T23:10:00.0") > 0
    assert parse_ts("2026-11-15") > 0
    assert parse_ts("20261115") == parse_ts("2026-11-15")
    assert parse_ts(1789318758000) == 1789318758
    assert parse_ts(0) == 0
    assert parse_ts(None) == 0


class HrvClient:
    def get_hrv_data(self, date_str):
        return {
            "calendarDate": "2026-11-15",
            "hrvStatus": "LOW",
            "avgOvernightHrv": 31.0,
            "hrvReadings": [
                {"hrvValue": 28, "readingTimeGMT": "2026-11-14T23:10:00.0"},
                {"hrvValue": 32, "readingTimeGMT": "2026-11-15T00:10:00.0"},
                {"hrvValue": 36, "readingTimeGMT": "2026-11-15T01:10:00.0"},
            ],
        }


def test_hrv_nightly_mean_and_date(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    extract_hrv(
        HrvClient(),
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    readings = _read(tmp_path, "hrv")
    nightly = _read(tmp_path, "hrv_nightly")
    assert len(readings) == 3
    assert len(nightly) == 1
    assert int(nightly.iloc[0]["hrvDate"]) > 0
    assert nightly.iloc[0]["hrvNightMean"] == 31.0  # Garmin overnight avg wins
    assert nightly.iloc[0]["hrvStatus"] == "LOW"
    assert readings.iloc[0]["hrvValue"] == 28


class StressClient:
    def get_stress_data(self, date_str):
        return {
            "calendarDate": "2026-11-15",
            "stressValuesArray": [
                [1789318800000, -1],
                [1789319700000, 20],
                [1789320600000, 40],
            ],
            "restStressDuration": 3600,
        }


def test_stress_samples_and_summary(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    extract_stress(
        StressClient(),
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    samples = _read(tmp_path, "stress_samples")
    summary = _read(tmp_path, "stress")
    assert len(samples) == 2
    assert summary.iloc[0]["averageStress"] == 30
    assert summary.iloc[0]["restStressSeconds"] == 3600


class BatteryClient:
    def get_body_battery(self, start, end):
        return [
            {
                "date": "2026-11-15",
                "startTimestampGMT": "2026-11-15T00:00:00.0",
                "charged": 40,
                "drained": 55,
                "bodyBatteryValuesArray": [
                    [1789318800000, 50],
                    [1789319700000, 45],
                    [1789320600000, 30],
                ],
            }
        ]


def test_body_battery_samples_and_no_zero_timestamp(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    extract_body_battery(
        BatteryClient(),
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    samples = _read(tmp_path, "body_battery_samples")
    summary = _read(tmp_path, "body_battery")
    assert len(samples) == 3
    assert summary.iloc[0]["bodyBatteryOpen"] == 50
    assert summary.iloc[0]["bodyBatteryClose"] == 30
    assert summary.iloc[0]["timestamp"] > 0


class CompositionClient:
    def get_body_composition(self, start, end):
        return {
            "dateWeightList": [
                {
                    "date": "2026-11-15",
                    "weight": 87230,
                    "bmi": 24.1,
                    "bodyFat": 18.5,
                    "boneMass": 4929,
                    "muscleMass": 34430,
                }
            ]
        }


def test_body_composition_grams_to_kg(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    extract_body_composition(
        CompositionClient(),
        storage,
        datetime(2026, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    df = _read(tmp_path, "body_composition")
    assert len(df) == 1
    assert df.iloc[0]["weightKg"] == 87.23
    assert df.iloc[0]["boneMassKg"] == 4.929
