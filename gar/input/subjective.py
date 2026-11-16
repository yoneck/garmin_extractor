"""Subjective training context: how the athlete felt, not what the watch saw.

Garmin records physiology; it cannot record "legs heavy after travel" or
"motivation low". These entries are stored in the ``subjective`` table keyed by
local date so the LLM context builder can quote them next to objective data.

Usage::

    python -m gar log --date 2026-11-16 --sleep 3 --fatigue 4 \
        --soreness 2 --motivation 2 --stress 3 --mood 3 \
        --note "Travel day, slept badly" --tags travel,alcohol

Every rating is 1-5 where 1 = very bad / very low and 5 = very good / very high,
except ``--rpe`` which follows the classic 1-10 scale.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from gar.utils.storage import CsvStorage

RATING_FIELDS = ("sleep", "fatigue", "soreness", "motivation", "stress", "mood")

FIELD_MAP = {
    "sleep": "sleepQuality",
    "fatigue": "fatigue",
    "soreness": "soreness",
    "motivation": "motivation",
    "stress": "stress",
    "mood": "mood",
}


def _validate_rating(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if not 1 <= value <= 5:
        raise ValueError(f"--{name} must be between 1 and 5 (got {value})")
    return value


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_entry(
    day: date,
    ratings: dict[str, int | None],
    note: str | None = None,
    tags: list[str] | None = None,
    rpe: int | None = None,
    sleep_hours: float | None = None,
    weight_kg: float | None = None,
) -> dict:
    """Validate and normalise one subjective entry."""
    if rpe is not None and not 1 <= rpe <= 10:
        raise ValueError(f"--rpe must be between 1 and 10 (got {rpe})")
    if sleep_hours is not None and not 0 <= sleep_hours <= 24:
        raise ValueError(f"--sleep-hours must be between 0 and 24 (got {sleep_hours})")
    if weight_kg is not None and not 20 <= weight_kg <= 300:
        raise ValueError(f"--weight must be between 20 and 300 kg (got {weight_kg})")

    entry = {
        "date": day.isoformat(),
        "timestamp": int(
            datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc).timestamp()
        ),
        "source": "manual",
        "rpe": rpe,
        "sleepHours": sleep_hours,
        "weightKg": weight_kg,
        "note": note,
        "tags": json.dumps(sorted({tag.strip() for tag in tags or [] if tag.strip()})),
    }
    for short_name, column in FIELD_MAP.items():
        entry[column] = _validate_rating(short_name, ratings.get(short_name))
    entry["hasAnyRating"] = any(entry[column] is not None for column in FIELD_MAP.values())
    return entry


def save_entry(data_dir: Path, entry: dict) -> int:
    storage = CsvStorage(Path(data_dir))
    return storage.sync_id("subjective", [entry])


def load_entries(data_dir: Path, limit: int | None = None) -> list[dict]:
    import pandas as pd

    path = Path(data_dir) / "subjective" / "subjective.csv"
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    if "date" not in frame.columns:
        return []
    frame = frame.sort_values("date")
    if limit:
        frame = frame.tail(limit)
    return frame.to_dict(orient="records")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gar log",
        description="Record how you felt (subjective training context).",
    )
    parser.add_argument("--date", help="Local date YYYY-MM-DD (default: today).")
    parser.add_argument("--sleep", type=int, help="Sleep quality 1-5.")
    parser.add_argument("--fatigue", type=int, help="Fatigue 1-5 (5 = exhausted).")
    parser.add_argument("--soreness", type=int, help="Muscle soreness 1-5.")
    parser.add_argument("--motivation", type=int, help="Motivation 1-5.")
    parser.add_argument("--stress", type=int, help="Life stress 1-5 (5 = very high).")
    parser.add_argument("--mood", type=int, help="Mood 1-5.")
    parser.add_argument("--rpe", type=int, help="Session RPE 1-10.")
    parser.add_argument("--sleep-hours", type=float, dest="sleep_hours")
    parser.add_argument("--weight", type=float, dest="weight_kg")
    parser.add_argument("--note", help="Free text (travel, illness, alcohol, ...).")
    parser.add_argument(
        "--tags",
        help="Comma-separated tags, e.g. travel,alcohol,race-week.",
    )
    parser.add_argument("--data-dir", help="Override GARMIN_DATA_DIR.")
    parser.add_argument("--show", action="store_true", help="Print recent entries.")
    parser.add_argument("--json", dest="as_json", action="store_true")
    return parser


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.data_dir:
        data_dir = Path(args.data_dir)
    if data_dir is None:
        import os

        data_dir = Path(os.environ.get("GARMIN_DATA_DIR") or Path("garmin_data"))

    if args.show:
        entries = load_entries(data_dir, limit=14)
        if args.as_json:
            print(json.dumps(entries, indent=2, default=str))
        else:
            for entry in entries:
                print(_format_entry(entry))
        return 0

    day = _parse_date(args.date)
    ratings = {name: getattr(args, name) for name in RATING_FIELDS}
    tags = (args.tags or "").split(",")
    try:
        entry = build_entry(
            day,
            ratings,
            note=args.note,
            tags=tags,
            rpe=args.rpe,
            sleep_hours=args.sleep_hours,
            weight_kg=args.weight_kg,
        )
    except ValueError as error:
        print(f"error: {error}")
        return 2
    if not entry["hasAnyRating"] and not entry["note"] and entry["tags"] == "[]":
        print("error: provide at least one rating, --note or --tags")
        return 2

    save_entry(data_dir, entry)
    print(_format_entry(entry))
    return 0


def _format_entry(entry: dict) -> str:
    parts = [str(entry.get("date"))]
    for short_name, column in FIELD_MAP.items():
        value = entry.get(column)
        if value is not None and not _is_nan(value):
            parts.append(f"{short_name}={int(value)}")
    if entry.get("rpe") is not None and not _is_nan(entry.get("rpe")):
        parts.append(f"rpe={int(entry['rpe'])}")
    if entry.get("sleepHours") is not None and not _is_nan(entry.get("sleepHours")):
        parts.append(f"sleep_h={entry['sleepHours']}")
    if entry.get("note") and not _is_nan(entry.get("note")):
        parts.append(f'"{entry["note"]}"')
    tags = entry.get("tags")
    if tags and tags != "[]" and not _is_nan(tags):
        parts.append(f"tags={tags}")
    return " | ".join(parts)


def _is_nan(value) -> bool:
    try:
        return value != value
    except Exception:
        return False
