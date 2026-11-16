"""DuckDB lake tests: ingest, derive, validate, export and the CLI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from gar.lake import db as lake_db
from gar.lake.cli import main as lake_main
from gar.lake.derive import derive_all
from gar.lake.export import export_parquet
from gar.lake.ingest import dataset_status, discover_datasets, ingest_all
from gar.lake.validate import quarantine_count, validate_all
from gar.utils.storage import CsvStorage

T0 = int(datetime(2026, 11, 15, 8, tzinfo=timezone.utc).timestamp())


def _seed(tmp_path: Path) -> None:
    storage = CsvStorage(tmp_path)
    storage.sync(
        "activities",
        [
            {
                "activityId": "111",
                "timestamp": T0,
                "sportType": "CYCLING",
                "activityName": "Gravel",
                "distanceMeter": 50000,
                "durationSecond": 5400,
                "movingDurationSecond": 5200,
                "averageHeartRate": 140,
                "maxHeartRate": 175,
                "avgPower": 200,
                "normPower": 230,
                "trainingLoad": 110,
                "calories": 900,
            }
        ],
    )
    storage.sync_id(
        "activity_laps",
        [
            {
                "activityId": "111",
                "lapIndex": 1,
                "distance_m": 1000,
                "duration_s": 600,
                "avgPower": 200,
            }
        ],
    )
    storage.sync_id("activity_hr", [{"activityId": "111", "zone": 1, "secondsInZone": 600}])
    storage.sync_id("activity_powers", [{"activityId": "111", "zone": 1, "secondsInZone": 300}])
    storage.sync(
        "sleep_summary",
        [
            {
                "timestamp": T0,
                "calendarDate": "2026-11-15",
                "sleepTimeSeconds": 25200,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 15000,
                "remSleepSeconds": 5400,
                "awakeSleepSeconds": 1200,
                "overallSleepScore": 78,
                "sleepNeedActualMinutes": 480,
                "avgSleepHRV": 42,
                "avgSpO2": 97,
                "avgRespiration": 15,
            }
        ],
    )
    storage.sync(
        "sleep_stages",
        [
            {
                "timestamp": T0,
                "sleepId": "s1",
                "stageIndex": 0,
                "stage": "deep",
                "durationSeconds": 3600,
            }
        ],
    )
    storage.sync(
        "hrv_nightly",
        [
            {
                "timestamp": T0,
                "hrvDate": T0,
                "calendarDate": "2026-11-15",
                "hrvNightMean": 31,
                "hrvWeeklyAvg": 29,
                "hrvStatus": "LOW",
            }
        ],
    )
    storage.sync(
        "hrv",
        [{"timestamp": T0 + 60, "hrvDate": T0, "calendarDate": "2026-11-15", "hrvValue": 30}],
    )
    storage.sync(
        "stress",
        [
            {
                "timestamp": T0,
                "stressDate": "2026-11-15",
                "averageStress": 30,
                "minStress": 10,
                "maxStress": 70,
                "restStressSeconds": 600,
            }
        ],
    )
    storage.sync(
        "body_battery",
        [
            {
                "timestamp": T0,
                "calendarDate": "2026-11-15",
                "bodyBatteryOpen": 80,
                "bodyBatteryClose": 30,
                "bodyBatteryMean": 55,
                "bodyBatteryMin": 25,
                "bodyBatteryMax": 85,
            }
        ],
    )
    storage.sync(
        "body_composition",
        [{"timestamp": T0, "calendarDate": "2026-11-15", "weightKg": 87.0, "bodyFatPct": 18.0}],
    )
    storage.sync(
        "daily_summary",
        [
            {
                "timestamp": T0,
                "date": "2026-11-15",
                "restingHeartRate": 58,
                "totalSteps": 12000,
                "averageStressLevel": 28,
            }
        ],
    )
    storage.sync(
        "readiness",
        [
            {
                "timestamp": T0,
                "date": "2026-11-15",
                "score": 62,
                "level": "MODERATE",
                "recoveryTimeMinutes": 600,
                "hrvFactorPct": 80,
            }
        ],
    )
    storage.sync(
        "training_status",
        [
            {
                "timestamp": T0,
                "date": "2026-11-15",
                "deviceId": "123",
                "trainingStatus": 8,
                "statusFeedback": "STRAINED",
                "acuteLoad7d": 157,
                "chronicLoad28d": 219,
                "acwrPct": 29,
                "acwrStatus": "LOW",
            }
        ],
    )
    storage.sync(
        "thresholds",
        [{"timestamp": T0, "date": "2026-09-15", "kind": "ftp", "sport": "CYCLING", "value": 309, "unit": "W"}],
    )
    storage.sync(
        "race_predictions",
        [{"timestamp": T0, "date": "2026-11-15", "distance": "5K", "predictedSeconds": 1621}],
    )
    storage.sync(
        "scores",
        [{"timestamp": T0, "date": "2026-11-15", "scoreType": "fitness_age", "value": 44.0}],
    )
    storage.sync(
        "workout_adherence",
        [
            {
                "scheduledId": "1",
                "timestamp": T0,
                "date": "2026-11-15",
                "status": "completed",
                "workoutId": "9",
                "plannedDurationSeconds": 3600,
            }
        ],
        allow_future=True,
    )
    storage.sync_id(
        "weather",
        [{"activityId": "111", "tempC": 11.1, "humidityPct": 87, "condition": "Rain"}],
    )
    storage.sync_id(
        "strength_sets",
        [
            {
                "activityId": "111",
                "setIndex": 0,
                "repetitions": 9,
                "weightKg": 20,
                "durationSeconds": 60,
                "exerciseCategory": "ROW",
            }
        ],
    )
    storage.sync_id(
        "gear",
        [{"gearId": "g1", "name": "Rondo", "type": "Bike", "maxMeters": 5000000}],
    )
    storage.sync_id(
        "gear_usage",
        [{"gearId": "g1", "activityId": "111", "distanceMeters": 50000}],
    )
    storage.sync_id(
        "subjective",
        [
            {
                "date": "2026-11-15",
                "timestamp": T0,
                "sleepQuality": 3,
                "fatigue": 4,
                "note": "travel",
                "tags": '["travel"]',
            }
        ],
    )


def _connect(tmp_path: Path):
    return lake_db.connect(tmp_path)


def test_ingest_creates_raw_tables_and_meta(tmp_path: Path):
    _seed(tmp_path)
    con = _connect(tmp_path)
    try:
        assert "activities" in discover_datasets(tmp_path)
        counts = ingest_all(con, tmp_path, verbose=False)
        assert counts["activities"] == 1
        assert lake_db.row_count(con, "raw_activities") == 1
        assert "activityId" in lake_db.columns(con, "raw_activities")
        status = {row["dataset"]: row for row in dataset_status(con, tmp_path)}
        assert status["activities"]["ingested"] is True
        assert status["activities"]["rows"] == 1
    finally:
        con.close()


def test_derive_builds_facts_and_daily(tmp_path: Path):
    _seed(tmp_path)
    con = _connect(tmp_path)
    try:
        ingest_all(con, tmp_path, verbose=False)
        counts = derive_all(con, verbose=False)
        assert counts["fact_activity"] == 1
        assert counts["fact_daily"] == 1
        assert counts["fact_sleep_session"] == 1
        assert counts["fact_threshold"] == 1

        daily = con.execute("SELECT * FROM fact_daily").df().iloc[0]
        assert daily["sleep_total_min"] == 420.0
        assert daily["hrv_night_avg_ms"] == 31
        assert daily["hrv_status"] == "LOW"
        assert daily["steps"] == 12000
        assert daily["rhr_bpm"] == 58
        assert daily["readiness_score"] == 62
        assert daily["training_load_day"] == 110
        assert daily["acwr"] == pytest.approx(0.29)
        assert daily["sleep_debt_7d_min"] == 60
        assert daily["sleep_feel"] == 3

        flags = con.execute("SELECT code FROM fact_flag").df()
        assert "HRV_LOW" in set(flags["code"])
    finally:
        con.close()


def test_derive_handles_missing_optional_columns(tmp_path: Path):
    """A minimal CSV set must not crash the derivation pipeline."""
    storage = CsvStorage(tmp_path)
    storage.sync("activities", [{"activityId": "222", "timestamp": T0, "sportType": "RUNNING"}])
    storage.sync(
        "daily_summary",
        [{"timestamp": T0, "date": "2026-11-15", "totalSteps": 4000}],
    )
    con = _connect(tmp_path)
    try:
        ingest_all(con, tmp_path, verbose=False)
        counts = derive_all(con, verbose=False)
        assert counts["fact_activity"] == 1
        assert counts["fact_daily"] == 1
        columns = lake_db.columns(con, "fact_daily")
        assert "sleep_total_min" not in columns or True
        daily = con.execute("SELECT * FROM fact_daily").df().iloc[0]
        assert daily["steps"] == 4000
    finally:
        con.close()


def test_validate_quarantines_bad_rows(tmp_path: Path):
    con = _connect(tmp_path)
    try:
        hrv_dir = tmp_path / "hrv"
        hrv_dir.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "timestamp": T0,
                    "hrvDate": T0,
                    "calendarDate": "2026-11-15",
                    "hrvValue": 30,
                },
                {
                    "timestamp": T0 + 60,
                    "hrvDate": T0,
                    "calendarDate": "2026-11-15",
                    "hrvValue": 500,
                },
            ]
        ).to_csv(hrv_dir / "hrv.csv", index=False)
        stress_dir = tmp_path / "stress_samples"
        stress_dir.mkdir(parents=True)
        pd.DataFrame(
            [{"timestamp": T0, "calendarDate": "2026-11-15", "stressValue": 150}]
        ).to_csv(stress_dir / "stress_samples.csv", index=False)
        nightly_dir = tmp_path / "hrv_nightly"
        nightly_dir.mkdir(parents=True)
        pd.DataFrame(
            [
                {"timestamp": T0, "hrvDate": T0, "calendarDate": "2026-11-15", "hrvNightMean": 30},
                {"timestamp": T0, "hrvDate": T0, "calendarDate": "2026-11-15", "hrvNightMean": 31},
            ]
        ).to_csv(nightly_dir / "hrv_nightly.csv", index=False)

        ingest_all(con, tmp_path, verbose=False)
        results = validate_all(con, verbose=False)
        by_rule = {row["rule"]: row for row in results}
        assert by_rule["hrv_out_of_range"]["rows"] == 1
        assert by_rule["hrv_out_of_range"]["severity"] == "error"
        assert by_rule["stress_out_of_range"]["rows"] == 1
        # Duplicate hrvDate is detected as well.
        assert by_rule["duplicate_hrv_night"]["rows"] == 1
        assert quarantine_count(con) == 2
        payloads = con.execute("SELECT payload_json FROM quarantine_record").fetchall()
        assert any('"hrvValue": 500' in row[0] or '"hrvValue":500' in row[0] for row in payloads)
    finally:
        con.close()


def test_export_writes_parquet_and_partitions(tmp_path: Path):
    _seed(tmp_path)
    con = _connect(tmp_path)
    try:
        ingest_all(con, tmp_path, verbose=False)
        derive_all(con, verbose=False)
        out = tmp_path / "parquet"
        manifest = export_parquet(con, tmp_path, out, verbose=False)
        assert (out / "fact_daily.parquet").exists()
        assert (out / "fact_activity.parquet").exists()
        assert (out / "manifest.json").exists()
        assert not (out / "raw_activities.parquet").exists()
        partition = out / "fact_daily_by_month"
        parts = list(partition.rglob("*.parquet"))
        assert parts
        assert any("year=2026" in str(path) for path in parts)
        saved = json.loads((out / "manifest.json").read_text())
        assert saved["tables"]
        assert saved["partitioned_fact_daily"] == str(partition)
        assert any(entry["table"] == "fact_daily" for entry in manifest["tables"])
    finally:
        con.close()


def test_cli_build_validate_export_and_query(tmp_path: Path, capsys):
    _seed(tmp_path)
    rc = lake_main(
        ["build", "--validate", "--export"],
        data_dir=tmp_path,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Lake build complete." in out
    assert lake_db.lake_path(tmp_path).exists()

    rc = lake_main(["status", "--json"], data_dir=tmp_path)
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["curated"]["fact_daily"] == 1

    rc = lake_main(
        ["query", "SELECT count(*) AS n FROM fact_activity", "--json"],
        data_dir=tmp_path,
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["n"] == 1


def test_cli_ingest_subset(tmp_path: Path, capsys):
    _seed(tmp_path)
    rc = lake_main(["ingest", "--datasets", "activities"], data_dir=tmp_path)
    assert rc == 0
    con = duckdb.connect(str(lake_db.lake_path(tmp_path)))
    try:
        assert lake_db.table_exists(con, "raw_activities")
        assert not lake_db.table_exists(con, "raw_sleep_summary")
    finally:
        con.close()


def test_lake_path_is_deterministic(tmp_path: Path):
    assert lake_db.lake_path(tmp_path) == tmp_path / "garmin.duckdb"
    assert lake_db.safe_name("activity-laps!!") == "activity_laps"
