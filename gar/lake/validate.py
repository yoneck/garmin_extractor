"""Lake validation: impossible values, duplicates and missing critical fields.

Failures are never silently dropped: offending rows are written to
``quarantine_record`` as JSON and summarised in ``data_quality_log``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import duckdb

from gar.lake.db import log_quality, quote_identifier, table_exists

# dataset/table, rule, SQL predicate that identifies BAD rows, severity
RULES: list[dict] = [
    {
        "table": "raw_activities",
        "rule": "timestamp_out_of_range",
        "requires": ["timestamp"],
        "where": "timestamp IS NOT NULL AND (timestamp <= 0 OR timestamp > epoch(current_timestamp + INTERVAL 1 DAY))",
        "severity": "error",
    },
    {
        "table": "raw_activities",
        "rule": "negative_distance",
        "requires": ["distanceMeter"],
        "where": "distanceMeter < 0",
        "severity": "error",
    },
    {
        "table": "raw_activities",
        "rule": "impossible_heart_rate",
        "requires": ["averageHeartRate", "maxHeartRate"],
        "where": "averageHeartRate < 0 OR averageHeartRate > 260 OR maxHeartRate > 260",
        "severity": "warn",
    },
    {
        "table": "raw_activities",
        "rule": "power_out_of_range",
        "requires": ["avgPower"],
        "where": "avgPower < 0 OR avgPower > 2500",
        "severity": "warn",
    },
    {
        "table": "raw_sleep_summary",
        "rule": "sleep_duration_out_of_range",
        "requires": ["sleepTimeSeconds"],
        "where": "sleepTimeSeconds < 0 OR sleepTimeSeconds > 86400",
        "severity": "error",
    },
    {
        "table": "raw_sleep_summary",
        "rule": "sleep_stage_sum_exceeds_total",
        "requires": [
            "deepSleepSeconds",
            "lightSleepSeconds",
            "remSleepSeconds",
            "sleepTimeSeconds",
        ],
        "where": "(deepSleepSeconds + lightSleepSeconds + remSleepSeconds) > sleepTimeSeconds * 1.05",
        "severity": "warn",
    },
    {
        "table": "raw_hrv",
        "rule": "hrv_out_of_range",
        "requires": ["hrvValue"],
        "where": "hrvValue IS NOT NULL AND (hrvValue <= 0 OR hrvValue > 300)",
        "severity": "error",
    },
    {
        "table": "raw_stress_samples",
        "rule": "stress_out_of_range",
        "requires": ["stressValue"],
        "where": "stressValue < 0 OR stressValue > 100",
        "severity": "warn",
    },
    {
        "table": "raw_body_battery_samples",
        "rule": "body_battery_out_of_range",
        "requires": ["bodyBatteryValue"],
        "where": "bodyBatteryValue < 0 OR bodyBatteryValue > 100",
        "severity": "warn",
    },
    {
        "table": "raw_body_composition",
        "rule": "weight_out_of_range",
        "requires": ["weightKg"],
        "where": "weightKg IS NOT NULL AND (weightKg < 20 OR weightKg > 300)",
        "severity": "error",
    },
    {
        "table": "raw_body_composition",
        "rule": "body_fat_out_of_range",
        "requires": ["bodyFatPct"],
        "where": "bodyFatPct IS NOT NULL AND (bodyFatPct < 0 OR bodyFatPct > 70)",
        "severity": "warn",
    },
    {
        "table": "raw_daily_summary",
        "rule": "negative_steps",
        "requires": ["totalSteps"],
        "where": "totalSteps < 0",
        "severity": "error",
    },
    {
        "table": "raw_weather",
        "rule": "humidity_out_of_range",
        "requires": ["humidityPct"],
        "where": "humidityPct < 0 OR humidityPct > 100",
        "severity": "warn",
    },
    {
        "table": "raw_thresholds",
        "rule": "non_positive_threshold",
        "requires": ["value"],
        "where": "value <= 0",
        "severity": "warn",
    },
    {
        "table": "raw_workout_adherence",
        "rule": "unknown_status",
        "requires": ["status"],
        "where": "status NOT IN ('completed', 'missed', 'upcoming')",
        "severity": "warn",
    },
    {
        "table": "raw_race_predictions",
        "rule": "non_positive_prediction",
        "requires": ["predictedSeconds"],
        "where": "predictedSeconds <= 0",
        "severity": "warn",
    },
    {
        "table": "fact_daily",
        "rule": "sleep_efficiency_out_of_range",
        "requires": ["sleep_efficiency_pct"],
        "where": "sleep_efficiency_pct < 0 OR sleep_efficiency_pct > 100",
        "severity": "warn",
    },
    {
        "table": "fact_daily",
        "rule": "extreme_hrv_zscore",
        "requires": ["hrv_zscore"],
        "where": "abs(hrv_zscore) > 5",
        "severity": "warn",
    },
    {
        "table": "raw_subjective",
        "rule": "rating_out_of_range",
        "requires": ["sleepQuality", "fatigue", "mood", "rpe"],
        "where": (
            "(sleepQuality IS NOT NULL AND (sleepQuality < 1 OR sleepQuality > 5)) OR "
            "(fatigue IS NOT NULL AND (fatigue < 1 OR fatigue > 5)) OR "
            "(mood IS NOT NULL AND (mood < 1 OR mood > 5)) OR "
            "(rpe IS NOT NULL AND (rpe < 1 OR rpe > 10))"
        ),
        "severity": "error",
    },
]

DUPLICATE_RULES = [
    ("raw_activities", ["activityId"], "duplicate_activity"),
    ("raw_sleep_summary", ["calendarDate"], "duplicate_sleep_day"),
    ("raw_hrv_nightly", ["hrvDate"], "duplicate_hrv_night"),
]


def _quarantine(
    con: duckdb.DuckDBPyConnection,
    dataset: str,
    reason: str,
    frame,
    source_file: str | None = None,
) -> None:
    if frame is None or len(frame) == 0:
        return
    for row in frame.to_dict(orient="records"):
        payload = json.dumps(row, default=str, ensure_ascii=False)
        key = hashlib.sha256((dataset + reason + payload).encode("utf-8")).hexdigest()[:32]
        con.execute(
            """
            INSERT OR IGNORE INTO quarantine_record
                (quarantine_id, dataset, reason, payload_json, source_file)
            VALUES (?, ?, ?, ?, ?)
            """,
            [key, dataset, reason, payload, source_file],
        )


def validate_all(con: duckdb.DuckDBPyConnection, verbose: bool = True) -> list[dict]:
    results: list[dict] = []
    for rule in RULES:
        table = rule["table"]
        if not table_exists(con, table):
            continue
        name = table.removeprefix("raw_")
        present = _columns(con, table)
        required = rule.get("requires") or []
        missing = [column for column in required if column not in present]
        if missing:
            log_quality(
                con,
                name,
                rule["rule"] + "_skipped",
                "info",
                0,
                "missing columns: " + ",".join(missing),
            )
            continue
        try:
            bad = con.execute(
                f"SELECT * FROM {quote_identifier(table)} WHERE {rule['where']}"
            ).df()
        except Exception as error:  # noqa: BLE001
            print(f"  validate {name}/{rule['rule']} error: {error}")
            continue
        count = len(bad)
        log_quality(con, name, rule["rule"], rule["severity"], count)
        if count:
            _quarantine(con, name, rule["rule"], bad)
        results.append(
            {
                "dataset": name,
                "rule": rule["rule"],
                "severity": rule["severity"],
                "rows": count,
            }
        )

    for table, keys, rule in DUPLICATE_RULES:
        if not table_exists(con, table):
            continue
        name = table.removeprefix("raw_")
        existing = [key for key in keys if key in _columns(con, table)]
        if not existing:
            continue
        key_list = ", ".join(quote_identifier(key) for key in existing)
        try:
            count = int(
                con.execute(
                    f"""
                    SELECT count(*) FROM (
                        SELECT {key_list}, count(*) AS n
                        FROM {quote_identifier(table)}
                        GROUP BY {key_list}
                        HAVING count(*) > 1
                    )
                    """
                ).fetchall()[0][0]
            )
        except Exception as error:  # noqa: BLE001
            print(f"  validate {name}/{rule} error: {error}")
            continue
        log_quality(con, name, rule, "warn", count)
        results.append(
            {"dataset": name, "rule": rule, "severity": "warn", "rows": count}
        )

    if verbose:
        for result in results:
            if result["rows"]:
                print(
                    f"  {result['dataset']:24s} {result['rule']:32s} "
                    f"{result['rows']:6d} rows ({result['severity']})"
                )
        errors = sum(r["rows"] for r in results if r["severity"] == "error")
        warns = sum(r["rows"] for r in results if r["severity"] != "error")
        print(f"  validation: {errors} error rows, {warns} warning rows")
    con.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('validate.last_run_utc', ?)",
        [datetime.now(timezone.utc).isoformat()],
    )
    return results


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()]


def quarantine_count(con: duckdb.DuckDBPyConnection) -> int:
    if not table_exists(con, "quarantine_record"):
        return 0
    return int(con.execute("SELECT count(*) FROM quarantine_record").fetchall()[0][0])
