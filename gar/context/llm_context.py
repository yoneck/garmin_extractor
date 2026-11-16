"""Build a compact markdown/JSON context document for an LLM from CSV storage."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from gar.context.coverage import coverage_report, read_folder, render_coverage

LOGGER = logging.getLogger(__name__)

SUMMARY_CONTRACT: dict[str, tuple[str, ...]] = {
    "activities": ("timestamp", "sportType", "distanceMeter", "durationSecond"),
    "sleep_summary": ("timestamp", "sleepTimeSeconds"),
    "hrv": ("timestamp", "hrvMean"),
    "stress": ("timestamp", "averageStress"),
    "body_battery": ("timestamp", "bodyBatteryClose"),
    "body_composition": ("timestamp", "weightKg"),
}


def generate_context(data_dir: Path) -> dict:
    summary = _summary(Path(data_dir))
    summary["generated_at"] = datetime.now().isoformat()
    return summary


def _summary(data_dir: Path) -> dict:
    parts: list[str] = []

    activities = _read(data_dir / "activities")
    if not activities.empty:
        _require(activities, SUMMARY_CONTRACT["activities"], "activities")
        part = ["## Activities", f"- Count: {len(activities)}"]
        if "sportType" in activities.columns:
            counts = activities["sportType"].value_counts().head(12).to_dict()
            counts = {k: int(v) for k, v in counts.items()}
            part += [f"  - {key}: {value}" for key, value in counts.items()]
        distance = _numeric(activities, "distanceMeter")
        duration = _numeric(activities, "durationSecond")
        load = _numeric(activities, "trainingLoad")
        if distance.notna().any():
            part.append(f"- Total distance (km): {distance.sum() / 1000:.1f}")
        if duration.notna().any():
            part.append(f"- Total duration (hours): {duration.sum() / 3600:.1f}")
        if load.notna().any():
            part.append(f"- Total training load: {load.sum():.0f}")
        if "timestamp" in activities.columns:
            last = pd.to_datetime(activities["timestamp"], unit="s", errors="coerce").max()
            if pd.notna(last):
                part.append(f"- Last activity: {last.date()}")
        parts += part

    sleep = _read(data_dir / "sleep_summary")
    if not sleep.empty:
        _require(sleep, SUMMARY_CONTRACT["sleep_summary"], "sleep_summary")
        part = ["## Sleep", f"- Nights: {len(sleep)}"]
        total_seconds = _numeric(sleep, "sleepTimeSeconds")
        if total_seconds.notna().any():
            part.append(f"- Avg sleep (min): {total_seconds.mean() / 60:.0f}")
            part.append(f"- Avg sleep (hours): {total_seconds.mean() / 3600:.1f}")
        score = _numeric(sleep, "overallSleepScore")
        if score.notna().any():
            part.append(f"- Avg sleep score: {score.mean():.0f}")
        deep = _numeric(sleep, "deepSleepSeconds")
        rem = _numeric(sleep, "remSleepSeconds")
        if total_seconds.notna().any() and total_seconds.mean() > 0:
            if deep.notna().any():
                part.append(f"- Deep sleep: {100 * deep.mean() / total_seconds.mean():.0f}%")
            if rem.notna().any():
                part.append(f"- REM sleep: {100 * rem.mean() / total_seconds.mean():.0f}%")
        if "calendarDate" in sleep.columns:
            part.append(
                f"- Range: {sleep['calendarDate'].min()} .. {sleep['calendarDate'].max()}"
            )
        parts += part

    hrv = _read(data_dir / "hrv_nightly")
    if hrv.empty:
        hrv = _read(data_dir / "hrv")
    if not hrv.empty:
        part = ["## HRV"]
        if "hrvDate" in hrv.columns:
            days = pd.to_datetime(
                pd.to_numeric(hrv["hrvDate"], errors="coerce"), unit="s", errors="coerce"
            ).dt.date.nunique()
        elif "timestamp" in hrv.columns:
            days = pd.to_datetime(
                pd.to_numeric(hrv["timestamp"], errors="coerce"), unit="s", errors="coerce"
            ).dt.date.nunique()
        else:
            days = len(hrv)
        part.append(f"- Days: {days}")
        column = "hrvNightMean" if "hrvNightMean" in hrv.columns else "hrvMean"
        mean = _numeric(hrv, column)
        if mean.notna().any():
            part.append(f"- Avg nightly HRV (ms): {mean.mean():.0f}")
        if "hrvStatus" in hrv.columns:
            statuses = hrv["hrvStatus"].dropna().value_counts().to_dict()
            if statuses:
                part.append(
                    "- Status: "
                    + ", ".join(f"{k}={int(v)}" for k, v in statuses.items())
                )
        parts += part

    stress = _read(data_dir / "stress")
    if not stress.empty:
        _require(stress, SUMMARY_CONTRACT["stress"], "stress")
        part = ["## Stress", f"- Days: {len(stress)}"]
        avg = _numeric(stress, "averageStress")
        if avg.notna().any():
            part.append(f"- Avg stress: {avg.mean():.0f}")
        max_stress = _numeric(stress, "maxStress")
        if max_stress.notna().any():
            part.append(f"- Avg daily max: {max_stress.mean():.0f}")
        parts += part

    battery = _read(data_dir / "body_battery")
    if not battery.empty:
        _require(battery, SUMMARY_CONTRACT["body_battery"], "body_battery")
        part = ["## Body Battery", f"- Days: {len(battery)}"]
        close = _numeric(battery, "bodyBatteryClose")
        if close.notna().any():
            part.append(f"- Avg end-of-day: {close.mean():.0f}")
        wake = _numeric(battery, "bodyBatteryOpen")
        if wake.notna().any():
            part.append(f"- Avg wake level: {wake.mean():.0f}")
        mean = _numeric(battery, "bodyBatteryMean")
        if mean.notna().any():
            part.append(f"- Avg daily mean: {mean.mean():.0f}")
        parts += part

    composition = _read(data_dir / "body_composition")
    if not composition.empty:
        _require(composition, SUMMARY_CONTRACT["body_composition"], "body_composition")
        part = ["## Body Composition", f"- Records: {len(composition)}"]
        weight = _numeric(composition, "weightKg")
        if weight.notna().any():
            part.append(f"- Average weight: {weight.mean():.1f} kg")
            clean = composition.loc[weight.notna()].copy()
            clean["_date"] = pd.to_datetime(
                pd.to_numeric(clean["timestamp"], errors="coerce"), unit="s", errors="coerce"
            )
            clean = clean.dropna(subset=["_date"]).sort_values("_date")
            if len(clean) >= 2:
                part.append(f"- Latest weight: {clean.iloc[-1]['weightKg']:.1f} kg")
                part.append(
                    f"- Change since first record: "
                    f"{clean.iloc[-1]['weightKg'] - clean.iloc[0]['weightKg']:+.1f} kg"
                )
        parts += part

    if not parts:
        return {
            "empty": True,
            "generated_at": datetime.now().isoformat(),
            "content": "No data has been extracted yet.",
        }

    coverage = coverage_report(data_dir, days=90)
    coverage_text = render_coverage(coverage)
    content = "\n\n".join(parts)
    return {
        "empty": False,
        "generated_at": datetime.now().isoformat(),
        "content": content,
        "sections": parts,
        "coverage": coverage,
        "coverage_markdown": coverage_text,
    }


def render_markdown(data_dir: Path, context: dict) -> str:
    lines = [
        "# Garmin Data Summary (LLM context)",
        "",
        f"_Generated: {context.get('generated_at', 'n/a')}_",
        "",
        context.get("content", "No data yet.") + "\n",
    ]
    coverage = context.get("coverage_markdown")
    if coverage:
        lines += ["---", "", coverage]
    return "\n".join(lines)


def _read(folder: Path) -> pd.DataFrame:
    return read_folder(folder)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([], dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _require(frame: pd.DataFrame, columns: tuple[str, ...], section: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        LOGGER.warning("%s: missing expected columns %s", section, missing)
