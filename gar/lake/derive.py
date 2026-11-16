"""Curated fact tables derived from the raw CSV mirror.

Raw tables keep Garmin's original names (``distanceMeter``, ``avgPower``...).
This module renames them to unit-explicit columns (``distance_m``, ``avg_power_w``)
and builds the daily cross-source table plus data-quality flags.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from gar.lake.db import (
    columns,
    log_quality,
    quote_identifier,
    row_count,
    set_meta,
    table_exists,
)

# raw column -> curated column for activities
ACTIVITY_MAP = {
    "activityId": "activity_id",
    "sportType": "sport_type",
    "activityName": "name",
    "locationName": "location_name",
    "timestamp": "start_utc",
    "startTimeLocal": "start_local",
    "durationSecond": "duration_s",
    "movingDurationSecond": "moving_duration_s",
    "elapsedDurationSecond": "elapsed_duration_s",
    "distanceMeter": "distance_m",
    "elevationGainMeter": "elevation_gain_m",
    "elevationLossMeter": "elevation_loss_m",
    "averageHeartRate": "avg_hr_bpm",
    "maxHeartRate": "max_hr_bpm",
    "averageSpeedKmh": "avg_speed_kmh",
    "maxSpeedKmh": "max_speed_kmh",
    "avgPower": "avg_power_w",
    "normPower": "np_w",
    "maxPower": "max_power_w",
    "averageCadence": "avg_cadence",
    "maxCadence": "max_cadence",
    "avgStrideLength": "avg_stride_length_cm",
    "avgGroundContactTime": "avg_ground_contact_time_ms",
    "avgVerticalOscillation": "avg_vertical_oscillation_mm",
    "avgVerticalRatio": "avg_vertical_ratio_pct",
    "calories": "calories",
    "steps": "steps",
    "aerobicTrainingEffect": "aerobic_te",
    "anaerobicTrainingEffect": "anaerobic_te",
    "trainingEffectLabel": "training_effect_label",
    "trainingLoad": "training_load",
    "vo2max": "vo2max",
    "differenceBodyBattery": "difference_body_battery",
    "isPR": "is_pr",
    "workoutId": "workout_id",
    "deviceId": "device_id",
    "lapCount": "lap_count",
}

LAP_MAP = {
    "activityId": "activity_id",
    "lapIndex": "lap_index",
    "startTime": "start_utc",
    "duration_s": "duration_s",
    "movingDuration_s": "moving_duration_s",
    "distance_m": "distance_m",
    "avgSpeed_mps": "avg_speed_mps",
    "avgHR": "avg_hr_bpm",
    "maxHR": "max_hr_bpm",
    "avgPower": "avg_power_w",
    "maxPower": "max_power_w",
    "normalizedPower": "np_w",
    "bikeCadence": "avg_cadence",
    "leftBalance": "left_balance_pct",
    "rightBalance": "right_balance_pct",
}

SLEEP_MAP = {
    "sleepId": "sleep_id",
    "calendarDate": "date_local",
    "sleepStartTimestampGMT": "start_utc",
    "sleepEndTimestampGMT": "end_utc",
    "sleepTimeSeconds": "sleep_total_s",
    "napTimeSeconds": "nap_s",
    "deepSleepSeconds": "deep_s",
    "remSleepSeconds": "rem_s",
    "lightSleepSeconds": "light_s",
    "awakeSleepSeconds": "awake_s",
    "unmeasurableSleepSeconds": "unmeasurable_s",
    "overallSleepScore": "sleep_score",
    "sleepScoreQualifier": "sleep_quality",
    "sleepNeedActualMinutes": "sleep_need_min",
    "nextSleepNeedActualMinutes": "next_sleep_need_min",
    "sleepScoreFeedback": "sleep_score_feedback",
    "sleepScoreInsight": "sleep_score_insight",
    "avgHeartRate": "avg_hr_bpm",
    "avgSleepHRV": "avg_hrv_ms",
    "hrvStatus": "hrv_status",
    "avgSpO2": "avg_spo2_pct",
    "lowestSpO2": "lowest_spo2_pct",
    "avgRespiration": "avg_respiration",
    "lowestRespiration": "lowest_respiration",
    "highestRespiration": "highest_respiration",
    "restlessMoments": "restless_moments",
    "skinTempDeviationC": "skin_temp_deviation_c",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame | None:
    if not table_exists(con, table):
        return None
    return con.execute(f"SELECT * FROM {quote_identifier(table)}").df()


def _series(frame: pd.DataFrame | None, *names):
    if frame is None:
        return None
    for name in names:
        if name in frame.columns:
            return frame[name]
    return None


def _numeric(frame: pd.DataFrame | None, *names) -> pd.Series:
    series = _series(frame, *names)
    if frame is None:
        return pd.Series(dtype="float64")
    if series is None:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(series, errors="coerce")


def _int_series(frame: pd.DataFrame | None, *names) -> pd.Series:
    return _numeric(frame, *names).astype("Int64")


def _str_series(frame: pd.DataFrame | None, *names) -> pd.Series:
    series = _series(frame, *names)
    if frame is None:
        return pd.Series(dtype="object")
    if series is None:
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")
    return series.astype(str)


def _date_series(frame: pd.DataFrame | None) -> pd.Series:
    """Best-effort local date extraction from a raw frame."""
    if frame is None or frame.empty:
        return pd.Series(dtype="datetime64[ns]")
    for name in ("date_local", "calendarDate", "date", "summaryDate"):
        if name in frame.columns:
            return pd.to_datetime(frame[name], errors="coerce").dt.normalize()
    for name in ("timestamp", "startTime", "hrvDate"):
        if name in frame.columns:
            values = pd.to_numeric(frame[name], errors="coerce")
            unit = "ms" if values.max(skipna=True) and values.max() > 1e11 else "s"
            return pd.to_datetime(values, unit=unit, errors="coerce").dt.normalize()
    return pd.Series(dtype="datetime64[ns]")


def _timestamps(frame: pd.DataFrame | None, name: str) -> pd.Series:
    if frame is None or name not in frame.columns:
        return pd.Series(dtype="datetime64[ns]")
    series = frame[name]
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    values = pd.to_numeric(series, errors="coerce")
    if values.dropna().empty:
        return pd.Series(dtype="datetime64[ns]")
    unit = "ms" if values.max() > 1e11 else "s"
    return pd.to_datetime(values, unit=unit, errors="coerce")


def _rename(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for source, target in mapping.items():
        if source in frame.columns:
            out[target] = frame[source]
    return out


def _write(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> int:
    """Replace a curated table from a pandas frame (via a temp view)."""
    view = f"_{table}_df"
    con.register(view, frame)
    con.execute(
        f"CREATE OR REPLACE TABLE {quote_identifier(table)} AS SELECT * FROM {quote_identifier(view)}"
    )
    con.unregister(view)
    rows = row_count(con, table)
    set_meta(con, f"derive.{table}.rows", str(rows))
    return rows


def _fill_primary(frame: pd.DataFrame, target: str, fallback: str) -> None:
    """Prefer ``target`` and fill gaps from ``fallback`` when both exist."""
    if target not in frame.columns:
        if fallback in frame.columns:
            frame[target] = frame[fallback]
        return
    if fallback in frame.columns:
        frame[target] = frame[target].fillna(frame[fallback])


def _merge_daily(parts: list[pd.DataFrame]) -> pd.DataFrame:
    merged: pd.DataFrame | None = None
    for part in parts:
        if part is None or part.empty or "date_local" not in part.columns:
            continue
        part = part.dropna(subset=["date_local"]).drop_duplicates("date_local")
        merged = part if merged is None else merged.merge(
            part, on="date_local", how="outer", suffixes=("", "_dup")
        )
    if merged is None:
        return pd.DataFrame(columns=["date_local"])
    duplicate_columns = [c for c in merged.columns if c.endswith("_dup")]
    if duplicate_columns:
        merged = merged.drop(columns=duplicate_columns)
    return merged.sort_values("date_local").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Activity facts
# ---------------------------------------------------------------------------

def derive_activities(con: duckdb.DuckDBPyConnection) -> int:
    raw = _read(con, "raw_activities")
    if raw is None or raw.empty:
        return 0
    fact = _rename(raw, ACTIVITY_MAP)
    fact["activity_id"] = _int_series(fact, "activity_id")
    fact = fact.dropna(subset=["activity_id"]).drop_duplicates("activity_id", keep="last")
    fact["start_utc"] = _timestamps(raw.loc[fact.index], "timestamp").dt.tz_localize("UTC")
    if "start_local" in fact.columns:
        fact["start_local"] = pd.to_datetime(fact["start_local"], errors="coerce")
    # Garmin speeds arrive in km/h from the activities list endpoint.
    if "avg_speed_kmh" in fact.columns:
        fact["avg_speed_mps"] = pd.to_numeric(fact["avg_speed_kmh"], errors="coerce") / 3.6
    if "max_speed_kmh" in fact.columns:
        fact["max_speed_mps"] = pd.to_numeric(fact["max_speed_kmh"], errors="coerce") / 3.6

    # IF/TSS from power where FTP is known later; a simple proxy uses NP/FTP if
    # fact_threshold already exists, otherwise these stay NULL.
    fact["if_"] = np.nan
    fact["tss"] = np.nan
    if table_exists(con, "fact_threshold"):
        ftp = con.execute(
            "SELECT max(value) FROM fact_threshold WHERE kind = 'ftp'"
        ).fetchall()[0][0]
        if ftp:
            np_power = _numeric(fact, "np_w")
            duration = _numeric(fact, "moving_duration_s", "duration_s")
            intensity = np_power / float(ftp)
            fact["if_"] = intensity
            fact["tss"] = (duration * np_power * intensity) / (float(ftp) * 3600) * 100

    # Efficiency factor: power per heartbeat (higher is fitter for the same pace).
    np_power = _numeric(fact, "np_w", "avg_power_w")
    avg_hr = _numeric(fact, "avg_hr_bpm")
    fact["efficiency_factor"] = np.where(avg_hr > 0, np_power / avg_hr, np.nan)

    # Banister TRIMP from heart-rate reserve. Resting HR is unknown here, so a
    # population default is used; a proper RHR-based variant belongs in M3.
    duration_min = _numeric(fact, "moving_duration_s", "duration_s") / 60.0
    max_hr = _numeric(fact, "max_hr_bpm")
    resting = 60.0
    reserve = ((avg_hr - resting) / (max_hr - resting)).clip(lower=0, upper=1)
    fact["trimp"] = np.where(
        duration_min.notna() & reserve.notna(),
        duration_min * reserve * 0.64 * np.exp(1.92 * reserve),
        np.nan,
    )
    return _write(con, "fact_activity", fact.reset_index(drop=True))


def derive_laps(con: duckdb.DuckDBPyConnection) -> int:
    raw = _read(con, "raw_activity_laps")
    if raw is None or raw.empty:
        return 0
    fact = _rename(raw, LAP_MAP)
    fact["activity_id"] = _int_series(fact, "activity_id")
    fact["lap_index"] = _int_series(fact, "lap_index")
    fact = fact.dropna(subset=["activity_id", "lap_index"]).drop_duplicates(
        ["activity_id", "lap_index"], keep="last"
    )
    if "start_utc" in fact.columns:
        fact["start_utc"] = pd.to_datetime(fact["start_utc"], errors="coerce")
    return _write(con, "fact_lap", fact.reset_index(drop=True))


def derive_zones(con: duckdb.DuckDBPyConnection) -> int:
    frames = []
    for table, zone_type in (("raw_activity_hr", "hr"), ("raw_activity_powers", "power")):
        raw = _read(con, table)
        if raw is None or raw.empty:
            continue
        frame = pd.DataFrame(
            {
                "activity_id": pd.to_numeric(_series(raw, "activityId"), errors="coerce"),
                "zone_type": zone_type,
                "zone": pd.to_numeric(_series(raw, "zone"), errors="coerce"),
                "seconds": pd.to_numeric(_series(raw, "secondsInZone"), errors="coerce"),
                "low_boundary": pd.to_numeric(
                    _series(raw, "zoneLowBoundary"), errors="coerce"
                ),
            }
        )
        frames.append(frame)
    if not frames:
        return 0
    fact = pd.concat(frames, ignore_index=True).dropna(subset=["activity_id", "zone"])
    fact = fact.drop_duplicates(["activity_id", "zone_type", "zone"], keep="last")
    rows = _write(con, "fact_zone", fact.reset_index(drop=True))
    _attach_zone_time(con)
    return rows


def _attach_zone_time(con: duckdb.DuckDBPyConnection) -> None:
    """Add ``time_in_zone_1_min`` ... ``_5_min`` columns to ``fact_activity``."""
    if not table_exists(con, "fact_activity") or not table_exists(con, "fact_zone"):
        return
    zones = con.execute(
        """
        SELECT activity_id, zone, sum(seconds) AS seconds
        FROM fact_zone
        WHERE zone_type = 'hr' AND zone BETWEEN 1 AND 5 AND seconds IS NOT NULL
        GROUP BY activity_id, zone
        """
    ).df()
    for zone in range(1, 6):
        con.execute(
            f"ALTER TABLE fact_activity "
            f"ADD COLUMN IF NOT EXISTS time_in_zone_{zone}_min DOUBLE"
        )
    if zones.empty:
        return
    pivot = zones.pivot_table(
        index="activity_id", columns="zone", values="seconds", aggfunc="sum"
    ).fillna(0.0)
    frame = pd.DataFrame({"activity_id": pivot.index.astype("int64")})
    for zone in range(1, 6):
        values = (
            pivot[zone].to_numpy() / 60.0
            if zone in pivot.columns
            else np.zeros(len(pivot))
        )
        frame[f"time_in_zone_{zone}_min"] = values
    con.register("_zone_time", frame.reset_index(drop=True))
    try:
        assignments = ", ".join(
            f"time_in_zone_{zone}_min = _zone_time.time_in_zone_{zone}_min"
            for zone in range(1, 6)
        )
        con.execute(
            f"""
            UPDATE fact_activity SET {assignments}
            FROM _zone_time
            WHERE fact_activity.activity_id = _zone_time.activity_id
            """
        )
    finally:
        con.unregister("_zone_time")


# ---------------------------------------------------------------------------
# Sleep facts
# ---------------------------------------------------------------------------

def derive_sleep(con: duckdb.DuckDBPyConnection) -> int:
    raw = _read(con, "raw_sleep_summary")
    if raw is None or raw.empty:
        return 0
    fact = _rename(raw, SLEEP_MAP)
    fact["date_local"] = pd.to_datetime(fact["date_local"], errors="coerce").dt.date
    fact = fact.dropna(subset=["date_local"]).drop_duplicates("date_local", keep="last")
    for raw_name, target in (
        ("sleepStartTimestampGMT", "start_utc"),
        ("sleepEndTimestampGMT", "end_utc"),
    ):
        if raw_name in raw.columns:
            fact[target] = _timestamps(raw.loc[fact.index], raw_name)
    for source in ("sleep_total_s", "nap_s", "deep_s", "rem_s", "light_s", "awake_s"):
        if source in fact.columns:
            fact[source.replace("_s", "_min")] = pd.to_numeric(
                fact[source], errors="coerce"
            ) / 60.0

    total = pd.to_numeric(fact.get("sleep_total_s"), errors="coerce")
    deep = pd.to_numeric(fact.get("deep_s"), errors="coerce")
    rem = pd.to_numeric(fact.get("rem_s"), errors="coerce")
    light = pd.to_numeric(fact.get("light_s"), errors="coerce")
    awake = pd.to_numeric(fact.get("awake_s"), errors="coerce")
    in_bed = total + awake
    fact["sleep_efficiency_pct"] = np.where(in_bed > 0, 100 * total / in_bed, np.nan)
    fact["deep_pct"] = np.where(total > 0, 100 * deep / total, np.nan)
    fact["rem_pct"] = np.where(total > 0, 100 * rem / total, np.nan)
    fact["light_pct"] = np.where(total > 0, 100 * light / total, np.nan)
    return _write(con, "fact_sleep_session", fact.reset_index(drop=True))


def derive_sleep_parts(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    stages = 0
    samples = 0
    raw = _read(con, "raw_sleep_stages")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "sleep_id": _str_series(raw, "sleepId"),
                "stage_index": pd.to_numeric(_series(raw, "stageIndex"), errors="coerce"),
                "stage": _series(raw, "stage"),
                "start_utc": _timestamps(raw, "timestamp"),
                "duration_s": pd.to_numeric(_series(raw, "durationSeconds"), errors="coerce"),
            }
        )
        frame["end_utc"] = frame["start_utc"] + pd.to_timedelta(
            frame["duration_s"], unit="s"
        )
        stages = _write(con, "fact_sleep_stage", frame.dropna(subset=["sleep_id"]))
    raw = _read(con, "raw_sleep_samples")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "sleep_id": _str_series(raw, "sleepId"),
                "kind": _series(raw, "kind"),
                "ts_utc": _timestamps(raw, "timestamp"),
                "value": pd.to_numeric(_series(raw, "value"), errors="coerce"),
                "confidence": pd.to_numeric(_series(raw, "confidence"), errors="coerce"),
            }
        )
        samples = _write(con, "fact_sleep_sample", frame.dropna(subset=["sleep_id", "ts_utc"]))
    return stages, samples


# ---------------------------------------------------------------------------
# Time-series and body facts
# ---------------------------------------------------------------------------

def derive_samples(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_name, table, value_column in (
        ("raw_stress_samples", "fact_stress_sample", "stressValue"),
        ("raw_body_battery_samples", "fact_body_battery_sample", "bodyBatteryValue"),
    ):
        raw = _read(con, raw_name)
        if raw is None or raw.empty:
            counts[table] = 0
            continue
        frame = pd.DataFrame(
            {
                "ts_utc": _timestamps(raw, "timestamp"),
                "value": pd.to_numeric(_series(raw, value_column), errors="coerce"),
                "date_local": pd.to_datetime(_series(raw, "calendarDate"), errors="coerce").dt.date,
            }
        )
        frame = frame.dropna(subset=["ts_utc"]).drop_duplicates("ts_utc", keep="last")
        counts[table] = _write(con, table, frame.reset_index(drop=True))
    return counts


def derive_body_composition(con: duckdb.DuckDBPyConnection) -> int:
    raw = _read(con, "raw_body_composition")
    if raw is None or raw.empty:
        return 0
    frame = pd.DataFrame(
        {
            "ts_utc": _timestamps(raw, "timestamp"),
            "date_local": pd.to_datetime(_series(raw, "calendarDate"), errors="coerce").dt.date,
            "weight_kg": pd.to_numeric(_series(raw, "weightKg"), errors="coerce"),
            "bmi": pd.to_numeric(_series(raw, "bmi"), errors="coerce"),
            "body_fat_pct": pd.to_numeric(_series(raw, "bodyFatPct"), errors="coerce"),
            "body_water_pct": pd.to_numeric(_series(raw, "bodyWaterPct"), errors="coerce"),
            "bone_mass_kg": pd.to_numeric(_series(raw, "boneMassKg"), errors="coerce"),
            "muscle_mass_kg": pd.to_numeric(_series(raw, "muscleMassKg"), errors="coerce"),
            "body_fat_kg": pd.to_numeric(_series(raw, "bodyFatKg"), errors="coerce"),
            "body_score": pd.to_numeric(_series(raw, "bodyScore"), errors="coerce"),
            "physique_rating": pd.to_numeric(_series(raw, "physiqueRating"), errors="coerce"),
            "metabolic_age": pd.to_numeric(_series(raw, "metabolicAge"), errors="coerce"),
            "source_type": _series(raw, "sourceType"),
        }
    )
    frame = frame.dropna(subset=["ts_utc"]).drop_duplicates("ts_utc", keep="last")
    return _write(con, "fact_body_comp", frame.reset_index(drop=True))


def derive_readiness_status(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    readiness = 0
    status = 0
    raw = _read(con, "raw_readiness")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "score": pd.to_numeric(_series(raw, "score"), errors="coerce"),
                "level": _series(raw, "level"),
                "feedback_short": _series(raw, "feedbackShort"),
                "feedback_long": _series(raw, "feedbackLong"),
                "recovery_time_min": pd.to_numeric(
                    _series(raw, "recoveryTimeMinutes"), errors="coerce"
                ),
                "recovery_time_factor_pct": pd.to_numeric(
                    _series(raw, "recoveryTimeFactorPct"), errors="coerce"
                ),
                "hrv_factor_pct": pd.to_numeric(_series(raw, "hrvFactorPct"), errors="coerce"),
                "sleep_score_factor_pct": pd.to_numeric(
                    _series(raw, "sleepScoreFactorPct"), errors="coerce"
                ),
                "stress_factor_pct": pd.to_numeric(
                    _series(raw, "stressFactorPct"), errors="coerce"
                ),
                "acwr_factor_pct": pd.to_numeric(_series(raw, "acwrFactorPct"), errors="coerce"),
                "sleep_score": pd.to_numeric(_series(raw, "sleepScore"), errors="coerce"),
                "hrv_weekly_avg_ms": pd.to_numeric(
                    _series(raw, "hrvWeeklyAverage"), errors="coerce"
                ),
                "valid_sleep": _series(raw, "validSleep"),
            }
        )
        frame = frame.dropna(subset=["date_local"]).drop_duplicates("date_local", keep="last")
        readiness = _write(con, "fact_readiness", frame.reset_index(drop=True))

    raw = _read(con, "raw_training_status")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "device_id": _int_series(raw, "deviceId"),
                "training_status": pd.to_numeric(_series(raw, "trainingStatus"), errors="coerce"),
                "status_feedback": _series(raw, "statusFeedback"),
                "fitness_trend": pd.to_numeric(_series(raw, "fitnessTrend"), errors="coerce"),
                "fitness_trend_sport": _series(raw, "fitnessTrendSport"),
                "weekly_load": pd.to_numeric(_series(raw, "weeklyLoad"), errors="coerce"),
                "acute_load_7d": pd.to_numeric(_series(raw, "acuteLoad7d"), errors="coerce"),
                "chronic_load_28d": pd.to_numeric(
                    _series(raw, "chronicLoad28d"), errors="coerce"
                ),
                "acwr_pct": pd.to_numeric(_series(raw, "acwrPct"), errors="coerce"),
                "acwr_status": _series(raw, "acwrStatus"),
                "load_balance_feedback": _series(raw, "loadBalanceFeedback"),
                "load_aerobic_low": pd.to_numeric(_series(raw, "loadAerobicLow"), errors="coerce"),
                "load_aerobic_high": pd.to_numeric(_series(raw, "loadAerobicHigh"), errors="coerce"),
                "load_anaerobic": pd.to_numeric(_series(raw, "loadAnaerobic"), errors="coerce"),
                "load_target_min": pd.to_numeric(_series(raw, "loadTargetMin"), errors="coerce"),
                "load_target_max": pd.to_numeric(_series(raw, "loadTargetMax"), errors="coerce"),
                "vo2max_generic": pd.to_numeric(_series(raw, "vo2maxGeneric"), errors="coerce"),
                "vo2max_cycling": pd.to_numeric(_series(raw, "vo2maxCycling"), errors="coerce"),
            }
        )
        frame = frame.dropna(subset=["date_local", "device_id"]).drop_duplicates(
            ["date_local", "device_id"], keep="last"
        )
        status = _write(con, "fact_training_status", frame.reset_index(drop=True))
    return readiness, status


def derive_performance(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}

    raw = _read(con, "raw_thresholds")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "kind": _series(raw, "kind"),
                "value": pd.to_numeric(_series(raw, "value"), errors="coerce"),
                "unit": _series(raw, "unit"),
                "sport": _series(raw, "sport"),
                "source": "garmin",
            }
        )
        frame = frame.dropna(subset=["date_local", "kind", "value"]).drop_duplicates(
            ["date_local", "kind", "sport"], keep="last"
        )
        counts["fact_threshold"] = _write(con, "fact_threshold", frame.reset_index(drop=True))

    raw = _read(con, "raw_zone_definitions")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "sport": _series(raw, "sport"),
                "zone_type": _series(raw, "zoneType"),
                "zone": pd.to_numeric(_series(raw, "zone"), errors="coerce"),
                "low_boundary": pd.to_numeric(_series(raw, "lowerBound"), errors="coerce"),
                "high_boundary": pd.to_numeric(_series(raw, "upperBound"), errors="coerce"),
            }
        )
        frame = frame.dropna(subset=["date_local", "sport", "zone"]).drop_duplicates(
            ["date_local", "sport", "zone_type", "zone"], keep="last"
        )
        counts["fact_zone_def"] = _write(con, "fact_zone_def", frame.reset_index(drop=True))

    raw = _read(con, "raw_personal_records")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "record_id": _str_series(raw, "recordId"),
                "date_local": pd.to_datetime(
                    _timestamps(raw, "achievedAt"), errors="coerce"
                ).dt.date,
                "sport": _series(raw, "sport"),
                "record_type": _int_series(raw, "typeId"),
                "value": pd.to_numeric(_series(raw, "value"), errors="coerce"),
                "unit": "s",
                "activity_id": _int_series(raw, "activityId"),
            }
        )
        frame = frame.dropna(subset=["record_id"]).drop_duplicates("record_id", keep="last")
        counts["fact_record"] = _write(con, "fact_record", frame.reset_index(drop=True))

    raw = _read(con, "raw_race_predictions")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "distance": _series(raw, "distance"),
                "time_s": pd.to_numeric(_series(raw, "predictedSeconds"), errors="coerce"),
                "source": "garmin",
            }
        )
        frame = frame.dropna(subset=["date_local", "distance"]).drop_duplicates(
            ["date_local", "distance"], keep="last"
        )
        counts["fact_race_prediction"] = _write(
            con, "fact_race_prediction", frame.reset_index(drop=True)
        )

    raw = _read(con, "raw_scores")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "score_type": _series(raw, "scoreType"),
                "value": pd.to_numeric(_series(raw, "value"), errors="coerce"),
                "unit": "score",
            }
        )
        frame = frame.dropna(subset=["date_local", "score_type"]).drop_duplicates(
            ["date_local", "score_type"], keep="last"
        )
        counts["fact_score"] = _write(con, "fact_score", frame.reset_index(drop=True))
    return counts


def derive_plan(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}

    raw = _read(con, "raw_workouts")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "workout_id": _int_series(raw, "workoutId"),
                "name": _series(raw, "workoutName"),
                "sport": _series(raw, "sport"),
                "created_utc": pd.to_datetime(_series(raw, "createdDate"), errors="coerce"),
            }
        )
        frame = frame.dropna(subset=["workout_id"]).drop_duplicates("workout_id", keep="last")
        counts["fact_workout"] = _write(con, "fact_workout", frame.reset_index(drop=True))

    raw = _read(con, "raw_workout_adherence")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "scheduled_id": _str_series(raw, "scheduledId"),
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "workout_id": _int_series(raw, "workoutId"),
                "activity_id": _int_series(raw, "matchedActivityId"),
                "planned_duration_s": pd.to_numeric(
                    _series(raw, "plannedDurationSeconds"), errors="coerce"
                ),
                "status": _series(raw, "status"),
            }
        )
        frame = frame.dropna(subset=["scheduled_id"]).drop_duplicates(
            "scheduled_id", keep="last"
        )
        counts["fact_workout_adherence"] = _write(
            con, "fact_workout_adherence", frame.reset_index(drop=True)
        )

    raw = _read(con, "raw_weather")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "activity_id": _int_series(raw, "activityId"),
                "temp_c": pd.to_numeric(_series(raw, "tempC"), errors="coerce"),
                "apparent_temp_c": pd.to_numeric(_series(raw, "apparentTempC"), errors="coerce"),
                "humidity_pct": pd.to_numeric(_series(raw, "humidityPct"), errors="coerce"),
                "wind_speed_kmh": pd.to_numeric(_series(raw, "windSpeedKmh"), errors="coerce"),
                "wind_direction_deg": pd.to_numeric(
                    _series(raw, "windDirection"), errors="coerce"
                ),
                "condition": _series(raw, "condition"),
            }
        )
        frame = frame.dropna(subset=["activity_id"]).drop_duplicates(
            "activity_id", keep="last"
        )
        counts["fact_weather"] = _write(con, "fact_weather", frame.reset_index(drop=True))

    raw = _read(con, "raw_strength_sets")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "activity_id": _int_series(raw, "activityId"),
                "set_index": _int_series(raw, "setIndex"),
                "exercise": _series(raw, "exerciseCategory"),
                "reps": pd.to_numeric(_series(raw, "repetitions"), errors="coerce"),
                "weight_kg": pd.to_numeric(_series(raw, "weightKg"), errors="coerce"),
                "duration_s": pd.to_numeric(_series(raw, "durationSeconds"), errors="coerce"),
            }
        )
        frame["volume_kg"] = frame["reps"] * frame["weight_kg"]
        frame = frame.dropna(subset=["activity_id", "set_index"]).drop_duplicates(
            ["activity_id", "set_index"], keep="last"
        )
        counts["fact_strength_set"] = _write(
            con, "fact_strength_set", frame.reset_index(drop=True)
        )

    raw = _read(con, "raw_gear")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "gear_id": _str_series(raw, "gearId"),
                "name": _series(raw, "name"),
                "gear_type": _series(raw, "type"),
                "max_distance_m": pd.to_numeric(_series(raw, "maxMeters"), errors="coerce"),
                "created_utc": pd.to_datetime(_series(raw, "dateBegin"), errors="coerce"),
                "retired_utc": pd.to_datetime(_series(raw, "dateEnd"), errors="coerce"),
            }
        )
        frame = frame.dropna(subset=["gear_id"]).drop_duplicates("gear_id", keep="last")
        counts["fact_gear"] = _write(con, "fact_gear", frame.reset_index(drop=True))

    raw = _read(con, "raw_gear_usage")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "gear_id": _str_series(raw, "gearId"),
                "activity_id": _int_series(raw, "activityId"),
                "distance_m": pd.to_numeric(_series(raw, "distanceMeters"), errors="coerce"),
            }
        )
        frame = frame.dropna(subset=["gear_id", "activity_id"]).drop_duplicates(
            ["gear_id", "activity_id"], keep="last"
        )
        counts["fact_gear_usage"] = _write(
            con, "fact_gear_usage", frame.reset_index(drop=True)
        )

    raw = _read(con, "raw_subjective")
    if raw is not None and not raw.empty:
        frame = pd.DataFrame(
            {
                "date_local": pd.to_datetime(_series(raw, "date"), errors="coerce").dt.date,
                "rpe": pd.to_numeric(_series(raw, "rpe"), errors="coerce"),
                "mood": pd.to_numeric(_series(raw, "mood"), errors="coerce"),
                "soreness": pd.to_numeric(_series(raw, "soreness"), errors="coerce"),
                "sleep_feel": pd.to_numeric(_series(raw, "sleepQuality"), errors="coerce"),
                "fatigue": pd.to_numeric(_series(raw, "fatigue"), errors="coerce"),
                "stress_note": _series(raw, "note"),
                "notes": _series(raw, "tags"),
            }
        )
        frame = frame.dropna(subset=["date_local"]).drop_duplicates("date_local", keep="last")
        counts["fact_subjective"] = _write(con, "fact_subjective", frame.reset_index(drop=True))
    return counts


# ---------------------------------------------------------------------------
# Daily warehouse
# ---------------------------------------------------------------------------

def _intensity_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame | None:
    """Per-day low/high HR-zone minutes from ``fact_zone``."""
    if not (
        table_exists(con, "fact_activity")
        and table_exists(con, "fact_zone")
    ):
        return None
    frame = con.execute(
        """
        SELECT
            CAST(a.start_utc AS DATE) AS date_local,
            sum(
                CASE WHEN z.zone <= 2 THEN z.seconds ELSE 0 END
            ) / 60.0 AS intensity_low_min,
            sum(
                CASE WHEN z.zone >= 4 THEN z.seconds ELSE 0 END
            ) / 60.0 AS intensity_high_min
        FROM fact_activity AS a
        JOIN fact_zone AS z ON a.activity_id = z.activity_id
        WHERE z.zone_type = 'hr' AND z.seconds IS NOT NULL
        GROUP BY 1
        """
    ).df()
    if frame.empty:
        return None
    frame["date_local"] = pd.to_datetime(frame["date_local"], errors="coerce").dt.date
    total = frame["intensity_low_min"] + frame["intensity_high_min"]
    frame["intensity_low_pct"] = np.where(
        total > 0, 100 * frame["intensity_low_min"] / total, np.nan
    )
    frame["intensity_high_pct"] = np.where(
        total > 0, 100 * frame["intensity_high_min"] / total, np.nan
    )
    return frame.dropna(subset=["date_local"])


def derive_daily(con: duckdb.DuckDBPyConnection) -> int:
    parts: list[pd.DataFrame] = []

    summary = _read(con, "raw_daily_summary")
    if summary is not None and not summary.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(summary),
                    "resting_hr_summary": _numeric(summary, "restingHeartRate"),
                    "steps": _numeric(summary, "totalSteps"),
                    "distance_m": _numeric(summary, "totalDistanceMeters"),
                    "floors": _numeric(summary, "floorsAscended"),
                    "active_kcal": _numeric(summary, "activeKilocalories"),
                    "total_kcal": _numeric(summary, "totalKilocalories"),
                    "moderate_intensity_min": _numeric(summary, "moderateIntensityMinutes"),
                    "vigorous_intensity_min": _numeric(summary, "vigorousIntensityMinutes"),
                    "stress_avg_summary": _numeric(summary, "averageStressLevel"),
                    "stress_max_summary": _numeric(summary, "maxStressLevel"),
                    "stress_qualifier": _series(summary, "stressQualifier"),
                    "body_battery_wake": _numeric(summary, "bodyBatteryAtWakeTime"),
                    "body_battery_charged": _numeric(summary, "bodyBatteryChargedValue"),
                    "body_battery_drained": _numeric(summary, "bodyBatteryDrainedValue"),
                    "body_battery_max": _numeric(summary, "bodyBatteryHighestValue"),
                    "body_battery_min": _numeric(summary, "bodyBatteryLowestValue"),
                    "spo2_avg_pct": _numeric(summary, "averageSpo2"),
                    "spo2_min_pct": _numeric(summary, "lowestSpo2"),
                    "respiration_avg": _numeric(summary, "avgWakingRespirationValue"),
                    "respiration_min": _numeric(summary, "lowestRespirationValue"),
                    "respiration_max": _numeric(summary, "highestRespirationValue"),
                    "sedentary_min": _numeric(summary, "sedentarySeconds") / 60,
                    "highly_active_min": _numeric(summary, "highlyActiveSeconds") / 60,
                    "sleeping_min_summary": _numeric(summary, "sleepingSeconds") / 60,
                }
            )
        )

    sleep = _read(con, "fact_sleep_session")
    if sleep is not None and not sleep.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": pd.to_datetime(sleep["date_local"], errors="coerce"),
                    "sleep_total_min": _numeric(sleep, "sleep_total_min"),
                    "sleep_deep_min": _numeric(sleep, "deep_min"),
                    "sleep_rem_min": _numeric(sleep, "rem_min"),
                    "sleep_light_min": _numeric(sleep, "light_min"),
                    "sleep_awake_min": _numeric(sleep, "awake_min"),
                    "sleep_efficiency_pct": _numeric(sleep, "sleep_efficiency_pct"),
                    "sleep_score": _numeric(sleep, "sleep_score"),
                    "sleep_need_min": _numeric(sleep, "sleep_need_min"),
                    "sleep_quality": _series(sleep, "sleep_quality"),
                    "sleep_start_utc": _timestamps(sleep, "start_utc"),
                    "sleep_end_utc": _timestamps(sleep, "end_utc"),
                }
            )
        )

    hrv = _read(con, "raw_hrv_nightly")
    if hrv is not None and not hrv.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(hrv),
                    "hrv_night_avg_ms": _numeric(hrv, "hrvNightMean", "hrvMean"),
                    "hrv_weekly_avg_ms": _numeric(hrv, "hrvWeeklyAvg"),
                    "hrv_status": _series(hrv, "hrvStatus"),
                }
            )
        )

    rhr = _read(con, "raw_rhr")
    if rhr is not None and not rhr.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(rhr),
                    "rhr_bpm": _numeric(rhr, "restingHeartRate"),
                }
            )
        )

    readiness = _read(con, "raw_readiness")
    if readiness is not None and not readiness.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(readiness),
                    "readiness_score": _numeric(readiness, "score"),
                    "readiness_level": _series(readiness, "level"),
                    "recovery_time_min": _numeric(readiness, "recoveryTimeMinutes"),
                }
            )
        )

    status = _read(con, "raw_training_status")
    if status is not None and not status.empty:
        if "timestamp" in status.columns:
            status = status.sort_values("timestamp")
        status = status.drop_duplicates("date", keep="first")
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(status),
                    "training_status": _numeric(status, "trainingStatus"),
                    "training_status_feedback": _series(status, "statusFeedback"),
                    "acute_load_7d": _numeric(status, "acuteLoad7d"),
                    "chronic_load_28d": _numeric(status, "chronicLoad28d"),
                    "acwr": _numeric(status, "acwrPct") / 100.0,
                    "acwr_status": _series(status, "acwrStatus"),
                    "load_balance_feedback": _series(status, "loadBalanceFeedback"),
                }
            )
        )

    battery = _read(con, "raw_body_battery")
    if battery is not None and not battery.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(battery),
                    "body_battery_wake": _numeric(battery, "bodyBatteryOpen"),
                    "body_battery_end_of_day": _numeric(battery, "bodyBatteryClose"),
                    "body_battery_avg": _numeric(battery, "bodyBatteryMean"),
                    "body_battery_min_sample": _numeric(battery, "bodyBatteryMin"),
                    "body_battery_max_sample": _numeric(battery, "bodyBatteryMax"),
                }
            )
        )

    stress = _read(con, "raw_stress")
    if stress is not None and not stress.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(stress),
                    "stress_avg": _numeric(stress, "averageStress"),
                    "stress_min": _numeric(stress, "minStress"),
                    "stress_max": _numeric(stress, "maxStress"),
                    "stress_rest_min": _numeric(stress, "restStressSeconds") / 60,
                    "stress_low_min": _numeric(stress, "lowStressSeconds") / 60,
                    "stress_medium_min": _numeric(stress, "mediumStressSeconds") / 60,
                    "stress_high_min": _numeric(stress, "highStressSeconds") / 60,
                    "stress_very_high_min": _numeric(stress, "veryHighStressSeconds") / 60,
                }
            )
        )

    steps = _read(con, "raw_steps_daily")
    if steps is not None and not steps.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(steps),
                    "steps_range": _numeric(steps, "totalSteps"),
                    "distance_range_m": _numeric(steps, "totalDistanceMeters"),
                    "step_goal": _numeric(steps, "stepGoal"),
                }
            )
        )

    body = _read(con, "raw_body_composition")
    if body is not None and not body.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(body),
                    "weight_kg": _numeric(body, "weightKg"),
                    "body_fat_pct": _numeric(body, "bodyFatPct"),
                    "muscle_mass_kg": _numeric(body, "muscleMassKg"),
                    "body_water_pct": _numeric(body, "bodyWaterPct"),
                }
            )
        )

    subjective = _read(con, "raw_subjective")
    if subjective is not None and not subjective.empty:
        parts.append(
            pd.DataFrame(
                {
                    "date_local": _date_series(subjective),
                    "sleep_feel": _numeric(subjective, "sleepQuality"),
                    "fatigue": _numeric(subjective, "fatigue"),
                    "soreness": _numeric(subjective, "soreness"),
                    "motivation": _numeric(subjective, "motivation"),
                    "mood": _numeric(subjective, "mood"),
                    "note": _series(subjective, "note"),
                }
            )
        )

    daily = _merge_daily(parts)
    if daily.empty:
        return 0
    daily["date_local"] = pd.to_datetime(daily["date_local"], errors="coerce").dt.date

    # Preferred values when several sources carry the same metric.
    _fill_primary(daily, "steps", "steps_range")
    _fill_primary(daily, "distance_m", "distance_range_m")
    _fill_primary(daily, "body_battery_min", "body_battery_min_sample")
    _fill_primary(daily, "body_battery_max", "body_battery_max_sample")

    # Hero-lines with derived baselines and ratios.
    daily = daily.sort_values("date_local").reset_index(drop=True)
    hrv = _numeric(daily, "hrv_night_avg_ms")
    daily["hrv_baseline_28d_ms"] = hrv.rolling(28, min_periods=7).mean()
    std = hrv.rolling(28, min_periods=7).std()
    daily["hrv_zscore"] = (hrv - daily["hrv_baseline_28d_ms"]) / std.replace(0, np.nan)

    rhr = _numeric(daily, "rhr_bpm")
    if rhr.isna().all():
        rhr = _numeric(daily, "resting_hr_summary")
    daily["rhr_bpm"] = rhr
    daily["rhr_baseline_28d_bpm"] = rhr.rolling(28, min_periods=7).mean()
    daily["rhr_delta_bpm"] = rhr - daily["rhr_baseline_28d_bpm"]

    readiness = _numeric(daily, "readiness_score")
    daily["readiness_delta"] = readiness.diff()
    daily["readiness_baseline_7d"] = readiness.rolling(7, min_periods=3).mean()

    battery_wake = _numeric(daily, "body_battery_wake")
    daily["body_battery_wake_delta"] = battery_wake - battery_wake.rolling(
        7, min_periods=3
    ).mean()

    need = _numeric(daily, "sleep_need_min")
    total = _numeric(daily, "sleep_total_min")
    deficit = (need - total).clip(lower=0)
    daily["sleep_debt_7d_min"] = deficit.rolling(7, min_periods=1).sum()

    # Sleep regularity: standard deviation of the sleep midpoint. Times before
    # noon are shifted by 24h so a 23:30 and 00:30 bedtime compare correctly.
    start = _timestamps(daily, "sleep_start_utc")
    end = _timestamps(daily, "sleep_end_utc")
    midpoint = start + (end - start) / 2
    midpoint_min = midpoint.dt.hour * 60 + midpoint.dt.minute
    midpoint_min = midpoint_min.where(midpoint_min >= 12 * 60, midpoint_min + 24 * 60)
    daily["sleep_midpoint_min"] = midpoint_min
    daily["sleep_regularity_sd_min"] = midpoint_min.rolling(7, min_periods=3).std()

    # Training load from activities, then acute/chronic/monotony/strain.
    activities = _read(con, "fact_activity")
    if activities is not None and not activities.empty and "start_utc" in activities.columns:
        loads = pd.DataFrame(
            {
                "date_local": pd.to_datetime(activities["start_utc"], errors="coerce").dt.date,
                "load": _numeric(activities, "training_load").fillna(0),
            }
        )
        loads = loads.groupby("date_local", as_index=False)["load"].sum()
        daily = daily.merge(loads, on="date_local", how="left")
        daily["training_load_day"] = daily["load"].fillna(0)
        daily = daily.drop(columns=["load"])
        daily["ramp_rate"] = (
            daily["training_load_day"].rolling(7, min_periods=1).sum()
            - daily["training_load_day"].shift(7).rolling(7, min_periods=1).sum()
        )
        mean7 = daily["training_load_day"].rolling(7, min_periods=3).mean()
        std7 = daily["training_load_day"].rolling(7, min_periods=3).std()
        daily["monotony"] = mean7 / std7.replace(0, np.nan)
        daily["strain"] = daily["training_load_day"].rolling(7, min_periods=1).sum() * daily["monotony"]
        load_7d = daily["training_load_day"].rolling(7, min_periods=1).sum()
        load_28d_daily = daily["training_load_day"].rolling(28, min_periods=7).mean()
        daily["load_7d_sum"] = load_7d
        daily["load_28d_daily_avg"] = load_28d_daily
        daily["load_ratio_7d_28d"] = load_7d / (load_28d_daily * 7).replace(0, np.nan)
    else:
        daily["training_load_day"] = 0.0

    intensity = _intensity_frame(con)
    if intensity is not None and not intensity.empty:
        daily = daily.merge(intensity, on="date_local", how="left")
        for column in (
            "intensity_low_min",
            "intensity_high_min",
            "intensity_low_pct",
            "intensity_high_pct",
        ):
            daily[column] = pd.to_numeric(daily[column], errors="coerce").fillna(0.0)

    daily["schema_version"] = "3.0.0"
    daily["source"] = "garmin"
    daily["fetched_at_utc"] = datetime.now(timezone.utc)

    rows = _write(con, "fact_daily", daily.reset_index(drop=True))
    derive_flags(con)
    return rows


def derive_flags(con: duckdb.DuckDBPyConnection) -> int:
    daily = _read(con, "fact_daily")
    flags: list[dict] = []

    def add(day, severity, code, value, threshold, reason):
        if day is None or (isinstance(day, float) and np.isnan(day)):
            return
        flags.append(
            {
                "flag_id": f"{day}-{code}-{uuid.uuid4().hex[:8]}",
                "date_local": day,
                "severity": severity,
                "code": code,
                "value": None if value is None else float(value),
                "threshold": threshold,
                "reason": reason,
                "source": "lake",
            }
        )

    if daily is not None and not daily.empty:
        rhr_streak = 0
        short_sleep_streak = 0
        for row in daily.itertuples(index=False):
            day = getattr(row, "date_local")
            acwr = getattr(row, "acwr", None)
            if acwr is not None and not pd.isna(acwr):
                if acwr > 1.5:
                    add(day, "warn", "ACWR_HIGH", acwr, 1.5, "Acute:chronic load ratio above 1.5")
                elif acwr < 0.8:
                    add(day, "info", "ACWR_LOW", acwr, 0.8, "Acute:chronic load ratio below 0.8")
            status = getattr(row, "hrv_status", None)
            if isinstance(status, str) and status.upper() == "LOW":
                add(day, "warn", "HRV_LOW", None, None, "Nightly HRV status is LOW")
            hrv_z = getattr(row, "hrv_zscore", None)
            if hrv_z is not None and not pd.isna(hrv_z) and hrv_z < -1:
                add(day, "warn", "HRV_ZSCORE_LOW", hrv_z, -1, "HRV z-score below -1")
            debt = getattr(row, "sleep_debt_7d_min", None)
            if debt is not None and not pd.isna(debt) and debt > 120:
                add(day, "warn", "SLEEP_DEBT", debt, 120, "7-day sleep debt above 2h")
            if debt is not None and not pd.isna(debt) and debt > 300:
                add(day, "warn", "SLEEP_DEBT_HIGH", debt, 300, "7-day sleep debt above 5h")
            delta = getattr(row, "rhr_delta_bpm", None)
            if delta is not None and not pd.isna(delta) and delta > 5:
                add(day, "warn", "RHR_ELEVATED", delta, 5, "Resting HR more than 5 bpm above baseline")
                rhr_streak += 1
            else:
                rhr_streak = 0
            if rhr_streak == 3:
                add(day, "warn", "RHR_ELEVATED_3D", delta, 5, "Resting HR elevated for 3 days")
            sleep_total = getattr(row, "sleep_total_min", None)
            if (
                sleep_total is not None
                and not pd.isna(sleep_total)
                and sleep_total < 360
            ):
                short_sleep_streak += 1
            else:
                short_sleep_streak = 0
            if short_sleep_streak == 3:
                add(day, "warn", "SLEEP_SHORT_3D", sleep_total, 360, "Under 6h sleep for 3 nights")
            stress_avg = getattr(row, "stress_avg", None)
            if stress_avg is not None and not pd.isna(stress_avg) and stress_avg > 60:
                add(day, "warn", "STRESS_HIGH", stress_avg, 60, "Daily average stress above 60")
            ratio = getattr(row, "load_ratio_7d_28d", None)
            if ratio is not None and not pd.isna(ratio) and ratio > 1.3:
                add(day, "warn", "LOAD_RAMP_HIGH", ratio, 1.3, "7d load above 1.3x the 28d average")
            score = getattr(row, "readiness_score", None)
            if score is not None and not pd.isna(score) and score < 40:
                add(day, "warn", "READINESS_LOW", score, 40, "Training readiness below 40")
            bb = getattr(row, "body_battery_end_of_day", None)
            if bb is not None and not pd.isna(bb) and bb < 20:
                add(day, "info", "BODY_BATTERY_LOW", bb, 20, "End-of-day Body Battery below 20")

    adherence = _read(con, "fact_workout_adherence")
    if adherence is not None and not adherence.empty:
        missed = adherence[adherence["status"] == "missed"]
        for row in missed.itertuples(index=False):
            add(row.date_local, "warn", "WORKOUT_MISSED", None, None, "Planned workout not completed")

    if not flags:
        return 0
    return _write(con, "fact_flag", pd.DataFrame(flags))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def derive_all(con: duckdb.DuckDBPyConnection, verbose: bool = True) -> dict[str, int]:
    counts: dict[str, int] = {}
    steps = [
        ("fact_activity", derive_activities),
        ("fact_lap", derive_laps),
        ("fact_zone", derive_zones),
        ("fact_sleep_session", derive_sleep),
        ("fact_body_comp", derive_body_composition),
        ("fact_daily", derive_daily),
    ]
    for name, function in steps:
        try:
            result = function(con)
            counts[name] = int(result)
        except Exception as error:  # noqa: BLE001
            print(f"  derive {name} error: {error}")
            counts[name] = 0
    readiness, status = derive_readiness_status(con)
    counts["fact_readiness"] = readiness
    counts["fact_training_status"] = status
    stages = derive_sleep_parts(con)
    counts["fact_sleep_stage"], counts["fact_sleep_sample"] = stages
    counts.update(derive_samples(con))
    counts.update(derive_performance(con))
    counts.update(derive_plan(con))
    # Flags depend on daily + adherence.
    counts["fact_flag"] = row_count(con, "fact_flag")
    set_meta(con, "derive.last_run_utc", datetime.now(timezone.utc).isoformat())
    set_meta(con, "derive.counts", counts)
    if verbose:
        for name, rows in counts.items():
            print(f"  {name:26s} {rows:8d} rows")
    return counts
