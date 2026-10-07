"""Entry point for the gar extractor: ``python -m gar``."""

from __future__ import annotations

import argparse
import glob
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
from pathlib import Path

from gar.context.build_full_context import build as build_full_context_entry



def _build_full_context(data_dir: Path) -> None:
    out = build_full_context_entry()
    if out[1]:
        print(f"  full_context: {out[1]} activities -> {out[0]}")
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract training and health data from Garmin Connect.")
    parser.add_argument("--mode", choices=["real", "test"], default="real",
                        help="Real = live API, Test = mock client (default: real).")
    parser.add_argument("--days", type=int, default=30,
                        help="How many days back to extract (default: 30).")
    parser.add_argument("--resume", type=int, default=None,
                        metavar="N",
                        help="Resume from the last extracted N days "
                             "(default: re-download from --days).")
    parser.add_argument("--activity-depth", action="store_true",
                        help="Also download FIT files and per-activity laps/splits/zones.")
    parser.add_argument("--no-session", action="store_true",
                        help="Discard the saved Garmin session before logging in (force new MFA).")
    return parser


def _resolve_env_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / ".env"


def _skip_ids(client, storage, data_dir: Path, activity_ids: list[str]) -> set[str]:
    """Return the set of activity ids that already have depth data (laps/splits)."""
    activity_laps = storage.base_dir / "activity_laps" / "activity_laps.csv"
    activity_splits = storage.base_dir / "activity_splits" / "activity_splits.csv"
    already: set[str] = set()
    for path in (activity_laps, activity_splits):
        try:
            df = _read_id_csv(path)
        except Exception as error:
            print(f"  warn: cannot read depth file {path}: {error}")
            continue
        for value in df["activityId"].tolist():
            already.add(str(value))
    return {aid for aid in activity_ids if aid not in already}


def _read_id_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frames = []
    parent = path.parent
    for existing in glob.glob(str(parent / "*.csv")):
        try:
            frames.append(pd.read_csv(existing))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _run_activity_depth(client, storage, data_dir: Path, activity_ids: list[str], force: bool = False) -> None:
    """Download FIT files + laps/splits/HR/power zones per activity id."""
    from gar.extraction.activities_deep import extract_activity_depth

    if force:
        keep = list(activity_ids)
    else:
        keep = sorted(_skip_ids(client, storage, data_dir, activity_ids))
        print(f"Depth update: {len(keep)} of {len(activity_ids)} activities need detail data.")

    if not keep:
        print("All activities in window already have fit detail. Skipping.")
        return

    for activity_id in keep:
        try:
            activity = client.get_activity(activity_id)
        except Exception:
            print(f"  {activity_id}: failed to load ({sys.exc_info()[1]})")
            continue
        extract_activity_depth(client, storage, data_dir, activity)
    print(f"Completed FIT/lap/session work for {len(keep)} activities.")


def _window_ids(client, start_date: datetime, end_date: datetime) -> list[str]:
    """Return all activity ids within the extraction window (for depth pulls)."""
    from gar.extraction.activities import extract_activities
    try:
        acts = client.get_activities_by_date(
            start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
        )
        if not acts:
            return []
        return [str(a.get("activityId")) for a in acts if a.get("activityId")]
    except Exception as error:
        print(f"  error enumerating window activities: {error}")
        return []


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from gar.utils.config import load_from_env, as_optional
    from gar.utils.logging_util import setup_logging
    from gar.utils.storage import CsvStorage
    from gar.context.llm_context import generate_context, render_markdown
    from gar.extraction.login import connect, verify_login
    from gar.extraction.activities import extract_activities
    from gar.extraction.sleep import extract_sleep
    from gar.extraction.physiology import (
        extract_hrv, extract_stress, extract_body_battery, extract_body_composition,
    )

    if args.mode == "test":
        print("Mock mode: no network usage. Run setup_config.sh/.ps1 for '--mode real'.")
        return 0

    config = load_from_env(_resolve_env_path(), strict=True)
    logging_dir = Path(config.get("GARMIN_LOG_DIR") or Path(".gar_logs"))
    setup_logging(logging_dir)

    client = connect(config)
    login_ok = verify_login(config)
    print(login_ok)

    data_dir = Path(config.get("GARMIN_DATA_DIR") or Path("garmin_data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    storage = CsvStorage(data_dir)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    if args.resume is not None:
        last = storage.last_period("activities")
        if last is not None and last[1] >= end_date - timedelta(days=args.resume):
            start_date = last[1]
            print(f"Resuming from {start_date.strftime('%Y-%m-%d')} (activities).")
        else:
            print(f"Starting from {start_date.strftime('%Y-%m-%d')} (resume window too wide; full extraction).")

    activity_ids = extract_activities(client, storage, start_date, end_date)
    if activity_ids:
        print(f"Loaded {len(activity_ids)} activities.")
    else:
        print("No new activities to load.")

    extract_sleep(client, storage, start_date, end_date)
    extract_hrv(client, storage, start_date, end_date)
    extract_stress(client, storage, start_date, end_date)
    extract_body_battery(client, storage, start_date, end_date)

    body_comp_dataset = as_optional(config, "GARMIN_BODY_COMP_DATASET")
    extract_body_composition(client, storage, start_date, end_date)

    if args.activity_depth:
        window_ids = _window_ids(client, start_date, end_date)
        _run_activity_depth(client, storage, data_dir, sorted(window_ids))

    _build_full_context(data_dir)

    context = generate_context(data_dir)
    mark = render_markdown(data_dir, context)
    context_dir = Path(config.get("GARMIN_CONTEXT_DIR") or data_dir / "context")
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "summary.md").write_text(mark, encoding="utf-8")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
