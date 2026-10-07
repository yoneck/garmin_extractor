"""Build a JSON summary and markdown context document for an LLM from CSV storage.

Outputs live under ``context/`` next to the data. Kept neutral so callers can
localise any human-facing text.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


def generate_context(data_dir: Path) -> dict:
    summary = _summary(data_dir)
    if summary.get("empty", True):
        return {"empty": True, "generated_at": datetime.now().isoformat(), "content": "No data has been extracted yet."}
    summary["generated_at"] = datetime.now().isoformat()
    return summary


def _summary(data_dir: Path) -> dict:
    parts = []

    activities = _read(data_dir / "activities")
    if activities.shape[0] > 0:
        part = ["## Activities", f"- Count: {len(activities)}"]
        if "sportType" in activities.columns:
            counts = activities["sportType"].value_counts(). to_dict()
            # Keep values JSON serialisable (int/None) not numpy scalars.
            counts = {k: int(v) if pd.notna(v) else None for k, v in counts.items()}
            part += [f"  - {key}: {value}" for key, value in counts.items()]
        if "distanceMeter" in activities.columns and not pd.isna(activities["distanceMeter"].sum()):
            part.append(f"- Total distance (km): {_sum(activities, 'distanceMeter') / 1000:.1f}")
        if "durationSecond" in activities.columns and not pd.isna(activities["durationSecond"].sum()):
            part.append(f"- Total duration (hours): {_sum(activities, 'durationSecond') / 3600:.1f}")
        parts += part

    sleep = _read(data_dir / "sleep_summary")
    if sleep.shape[0] > 0:
        part = ["## Sleep", f"- Nights: {len(sleep)}"]
        if "totalSleepMinutes" in sleep.columns and not pd.isna(sleep["totalSleepMinutes"].mean()):
            part.append(f"- Avg sleep (min): {sleep['totalSleepMinutes'].mean():.0f}")
        if "sleepScore" in sleep.columns and not pd.isna(sleep["sleepScore"].mean()):
            part.append(f"- Avg sleep score: {_mean(sleep, 'sleepScore'):.0f}")
        parts += part

    hrv = _read(data_dir / "hrv")
    if hrv.shape[0] > 0:
        if "hrvDate" in hrv.columns:
            days = pd.to_datetime(hrv["hrvDate"], errors="coerce", unit="ms")
            unique_days = days.dt.date.nunique()
        elif "timestamp" in hrv.columns:
            days = pd.to_datetime(hrv["timestamp"], errors="coerce", unit="s")
            unique_days = days.dt.date.nunique()
        else:
            unique_days = len(hrv)
        part = ["## HRV", f"- Days: {unique_days}"]
        if "hrvMean" in hrv.columns and not pd.isna(hrv["hrvMean"].mean()):
            part.append(f"- Avg HRV: {_mean(hrv, 'hrvMean'):.0f}")
        parts += part

    stress = _read(data_dir / "stress")
    if stress.shape[0] > 0:
        part = ["## Stress", f"- Days: {len(stress)}"]
        if "averageStress" in stress.columns and not pd.isna(stress["averageStress"].mean()):
            part.append(f"- Avg stress: {_mean(stress, 'averageStress'):.0f}")
        parts += part

    battery = _read(data_dir / "body_battery")
    if battery.shape[0] > 0:
        part = ["## Body Battery", f"- Days: {len(battery)}"]
        if "bodyBatteryValue" in battery.columns and not pd.isna(battery["bodyBatteryValue"].mean()):
            part.append(f"- Avg end-of-day: {_mean(battery, 'bodyBatteryValue'):.0f}")
        parts += part

    composition = _read(data_dir / "body_composition")
    if composition.shape[0] > 0:
        part = ["## Body Composition", f"- Records: {len(composition)}"]
        if "weight" in composition.columns and not pd.isna(composition["weight"].mean()):
            part.append(f"- Average weight: {_mean(composition, 'weight')} kg")
        parts += part

    if not parts:
        return {"empty": True, "generated_at": datetime.now().isoformat(), "content": "No data has been extracted yet."}
    sections = parts
    return {"empty": False, "generated_at": datetime.now().isoformat(), "content": "\n\n".join(parts), "sections": sections}


def render_markdown(data_dir: Path, context: dict) -> str:
    lines = ["# Garmin Data Summary (LLM context)", "", context.get("content", "No data yet.") + "\n"]
    return "\n".join(lines)


def _read(folder: Path) -> pd.DataFrame:
    files = sorted(folder.glob("*.csv"))
    if not files:
        return pd.DataFrame()
    frames = []
    for file_path in files:
        try:
            frames.append(pd.read_csv(file_path))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _sum(df: pd.DataFrame, column: str) -> any:  # noqa: ANN401
    df[column] = pd.to_numeric(df[column], errors="coerce")
    return round(float(df[column].sum()), 1)


def _mean(df: pd.DataFrame, column: str) -> any:  # noqa: ANN401
    df[column] = pd.to_numeric(df[column], errors="coerce")
    return round(float(df[column].mean()), 1)
