"""Entry point for the gar extractor: ``python -m gar``."""

from __future__ import annotations

import argparse
import glob
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gar.context.build_full_context import build as build_full_context_entry
from gar.utils.progress import ProgressBar


def _build_full_context(data_dir: Path) -> None:
    out = build_full_context_entry(data_dir)
    if out[1]:
        print(f"  full_context: {out[1]} activities -> {out[0]}")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return parsed.replace(tzinfo=timezone.utc)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract training and health data from Garmin Connect."
    )
    parser.add_argument(
        "--mode",
        choices=["real", "test"],
        default="real",
        help="Real = live API, Test = mock client (default: real).",
    )
    parser.add_argument("--days", type=int, default=30, help="Days back to extract (default: 30).")
    parser.add_argument("--since", type=str, default=None, help="Start date YYYY-MM-DD (overrides --days).")
    parser.add_argument("--until", type=str, default=None, help="End date YYYY-MM-DD (default: today).")
    parser.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="N",
        help="Resume from the last successful watermark within N days.",
    )
    parser.add_argument(
        "--activity-depth",
        action="store_true",
        help="Also download per-activity laps/splits/zones.",
    )
    parser.add_argument("--fit", action="store_true", help="Download FIT/TCX files (implies depth).")
    parser.add_argument(
        "--fit-format",
        choices=["fit", "tcx", "gpx", "original"],
        default="fit",
        help="Activity file format for --fit (default: fit).",
    )
    parser.add_argument(
        "--datasets",
        type=str,
        default=None,
        help=(
            "Comma-separated subset: activities,daily,steps,sleep,hrv,stress,"
            "body_battery,body_composition,performance,plan."
        ),
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Also fetch workouts, adherence, gear, weather and strength sets.",
    )
    parser.add_argument(
        "--no-daily",
        action="store_true",
        help="Skip daily wellness/readiness/training status.",
    )
    parser.add_argument(
        "--no-performance",
        action="store_true",
        help="Skip thresholds, zones, records, race predictions and scores.",
    )
    parser.add_argument(
        "--hrv-readings",
        action="store_true",
        help="Also fetch per-night HRV readings (slow; short windows only).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore watermarks and re-download the selected window.",
    )
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="Discard the saved Garmin session before logging in (force new MFA).",
    )
    parser.add_argument("--no-progress", action="store_true", help="Disable the progress bar.")
    return parser


def _resolve_env_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / ".env"


def _default_data_dir() -> Path:
    """Data dir for non-sync subcommands (env first, no login required)."""
    from gar.utils.config import load_from_env

    config = load_from_env(_resolve_env_path(), strict=False)
    return Path(config.get("GARMIN_DATA_DIR") or Path("garmin_data"))


def _coverage_main(argv: list[str]) -> int:
    import json

    from gar.context.coverage import coverage_report, render_coverage

    parser = argparse.ArgumentParser(
        prog="gar coverage", description="Show local data coverage per dataset."
    )
    parser.add_argument("--days", type=int, default=90, help="Window size (default: 90).")
    parser.add_argument("--data-dir", help="Override GARMIN_DATA_DIR.")
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--out", help="Write the report to this file.")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir()
    report = coverage_report(data_dir, days=args.days)
    text = (
        json.dumps(report, indent=2)
        if args.as_json
        else render_coverage(report)
    )
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


def _skip_ids(storage, activity_ids: list[str]) -> set[str]:
    """Return the set of activity ids that already have depth data."""
    already: set[str] = set()
    for table in ("activity_laps", "activity_splits"):
        folder = storage.base_dir / table
        files = sorted(folder.glob("*.csv"))
        if not files:
            continue
        try:
            frames = [pd.read_csv(path, usecols=["activityId"]) for path in files]
        except Exception as error:
            print(f"  warn: cannot read depth table {table}: {error}")
            continue
        for frame in frames:
            already.update(frame["activityId"].astype(str).tolist())
    return {aid for aid in activity_ids if aid not in already}


def _run_activity_depth(
    client,
    storage,
    data_dir: Path,
    activity_ids: list[str],
    force: bool = False,
    download_fit: bool = False,
    fit_format: str = "fit",
) -> None:
    from gar.extraction.activities_deep import extract_activity_depth

    keep = list(activity_ids) if force else sorted(_skip_ids(storage, activity_ids))
    print(f"Depth update: {len(keep)} of {len(activity_ids)} activities need detail data.")
    if not keep:
        print("All activities in window already have detail. Skipping.")
        return

    for activity_id in keep:
        try:
            activity = client.get_activity(activity_id)
        except Exception:
            activity = {"activityId": activity_id}
        extract_activity_depth(
            client,
            storage,
            data_dir,
            activity,
            download_fit=download_fit,
            fit_format=fit_format,
        )
    print(f"Completed detail work for {len(keep)} activities.")


def _window_ids(client, start_date: datetime, end_date: datetime) -> list[str]:
    """Return all activity ids within the extraction window (for depth pulls)."""
    try:
        acts = client.get_activities_by_date(
            start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
        )
        return [str(a.get("activityId")) for a in (acts or []) if a.get("activityId")]
    except Exception as error:
        print(f"  error enumerating window activities: {error}")
        return []


def _resolve_window(args, state) -> tuple[datetime, datetime]:
    end_date = _parse_date(args.until) or datetime.now(timezone.utc)
    start_date = _parse_date(args.since) or (end_date - timedelta(days=args.days))

    if args.since is None and args.resume is not None and not args.force:
        watermark = state.watermark("activities")
        if watermark is not None and watermark >= end_date - timedelta(days=args.resume):
            start_date = watermark - timedelta(days=1)
            print(f"Resuming from watermark {start_date.date()} (activities).")
        else:
            start_date = end_date - timedelta(days=args.resume)
            print(f"Starting from {start_date.date()} (resume window too old).")

    if args.force:
        print(f"Force mode: {start_date.date()} -> {end_date.date()} (ignoring watermarks).")
    return start_date, end_date


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("log", "subjective"):
        from gar.input.subjective import main as log_main

        return log_main(argv[1:], data_dir=_default_data_dir())
    if argv and argv[0] == "coverage":
        return _coverage_main(argv[1:])
    if argv and argv[0] == "lake":
        from gar.lake.cli import main as lake_main

        return lake_main(argv[1:], data_dir=_default_data_dir())
    if argv and argv[0] == "context":
        from gar.context.cli import main as context_main

        return context_main(argv[1:], data_dir=_default_data_dir())
    if argv and argv[0] in ("rec", "recommend"):
        from gar.recommendations.ledger import main as rec_main

        return rec_main(argv[1:], data_dir=_default_data_dir())
    if argv and argv[0] == "sync":
        return main(argv[1:])
    if argv and argv[0] == "report" and len(argv) > 1 and argv[1] == "coverage":
        return main(["coverage", *argv[2:]])

    args = _build_parser().parse_args(argv)

    from gar.utils.config import load_from_env
    from gar.utils.logging_util import setup_logging
    from gar.utils.storage import CsvStorage
    from gar.utils.state import SyncState
    from gar.context.llm_context import generate_context, render_markdown
    from gar.extraction.login import connect, verify_login
    from gar.extraction.activities import extract_activities
    from gar.extraction.sleep import extract_sleep
    from gar.extraction.daily import extract_daily, extract_steps
    from gar.extraction.performance import extract_performance
    from gar.extraction.plan import extract_plan
    from gar.extraction.physiology import (
        extract_hrv,
        extract_stress,
        extract_body_battery,
        extract_body_composition,
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
    timezone_name = config.get("GARMIN_TIMEZONE") or "UTC"
    storage = CsvStorage(data_dir, timezone_name=timezone_name)
    state = SyncState(data_dir / "state" / "sync_state.json")

    start_date, end_date = _resolve_window(args, state)
    selected = None
    if args.datasets:
        selected = {name.strip() for name in args.datasets.split(",") if name.strip()}

    stages: list[str] = []

    def wanted(name: str) -> bool:
        return selected is None or name in selected

    if wanted("activities"):
        stages.append("activities")
    if wanted("daily") and not args.no_daily:
        stages.append("daily")
    if wanted("steps"):
        stages.append("steps")
    for name in ("sleep", "hrv", "stress", "body_battery", "body_composition"):
        if wanted(name):
            stages.append(name)
    if wanted("performance") and not args.no_performance:
        stages.append("performance")
    if args.plan or wanted("plan"):
        stages.append("plan")
    if args.activity_depth or args.fit:
        stages.append("activity_depth")
    stages.extend(["full_context", "context"])

    bar = ProgressBar(
        total=len(stages),
        label="sync",
        enabled=not args.no_progress,
    )
    bar.start()

    def mark(dataset: str, status: str = "ok", rows: int = 0, error: str | None = None) -> None:
        state.update(
            dataset,
            watermark=end_date if status == "ok" else None,
            status=status,
            rows=rows,
            error=error,
        )

    activity_ids: list[str] = []
    if "activities" in stages:
        try:
            activity_ids = extract_activities(client, storage, start_date, end_date)
            print(f"Loaded {len(activity_ids)} activities." if activity_ids else "No new activities.")
            mark("activities", rows=len(activity_ids))
        except Exception as error:
            print(f"  activities error: {error}")
            mark("activities", status="error", error=str(error))
        bar.advance("activities")

    if "daily" in stages:
        try:
            extract_daily(client, storage, start_date, end_date)
            mark("daily")
        except Exception as error:
            print(f"  daily error: {error}")
            mark("daily", status="error", error=str(error))
        bar.advance("daily")

    if "steps" in stages:
        try:
            rows = extract_steps(client, storage, start_date, end_date)
            print(f"  steps: {rows} days.")
            mark("steps", rows=rows)
        except Exception as error:
            print(f"  steps error: {error}")
            mark("steps", status="error", error=str(error))
        bar.advance("steps")

    if "sleep" in stages:
        try:
            extract_sleep(client, storage, start_date, end_date)
            mark("sleep")
        except Exception as error:
            print(f"  sleep error: {error}")
            mark("sleep", status="error", error=str(error))
        bar.advance("sleep")

    if "hrv" in stages:
        try:
            extract_hrv(
                client,
                storage,
                start_date,
                end_date,
                include_readings=args.hrv_readings,
            )
            mark("hrv")
        except Exception as error:
            print(f"  hrv error: {error}")
            mark("hrv", status="error", error=str(error))
        bar.advance("hrv")

    if "stress" in stages:
        try:
            extract_stress(client, storage, start_date, end_date)
            mark("stress")
        except Exception as error:
            print(f"  stress error: {error}")
            mark("stress", status="error", error=str(error))
        bar.advance("stress")

    if "body_battery" in stages:
        try:
            extract_body_battery(client, storage, start_date, end_date)
            mark("body_battery")
        except Exception as error:
            print(f"  body_battery error: {error}")
            mark("body_battery", status="error", error=str(error))
        bar.advance("body_battery")

    if "body_composition" in stages:
        try:
            extract_body_composition(client, storage, start_date, end_date)
            mark("body_composition")
        except Exception as error:
            print(f"  body_composition error: {error}")
            mark("body_composition", status="error", error=str(error))
        bar.advance("body_composition")

    if "performance" in stages:
        try:
            counts = extract_performance(client, storage, start_date, end_date)
            mark("performance", rows=sum(counts.values()))
        except Exception as error:
            print(f"  performance error: {error}")
            mark("performance", status="error", error=str(error))
        bar.advance("performance")

    if "plan" in stages:
        try:
            window_ids = activity_ids or _window_ids(client, start_date, end_date)
            extract_plan(
                client,
                storage,
                start_date,
                end_date,
                activity_ids=sorted(window_ids),
            )
            mark("plan")
        except Exception as error:
            print(f"  plan error: {error}")
            mark("plan", status="error", error=str(error))
        bar.advance("plan")

    if "activity_depth" in stages:
        try:
            window_ids = _window_ids(client, start_date, end_date)
            _run_activity_depth(
                client,
                storage,
                data_dir,
                sorted(window_ids),
                force=args.force,
                download_fit=args.fit,
                fit_format=args.fit_format,
            )
            mark("activity_depth")
        except Exception as error:
            print(f"  activity_depth error: {error}")
            mark("activity_depth", status="error", error=str(error))
        bar.advance("activity_depth")

    try:
        _build_full_context(data_dir)
        mark("full_context")
    except Exception as error:
        print(f"  full_context error: {error}")
        mark("full_context", status="error", error=str(error))
    bar.advance("full_context")

    try:
        context = generate_context(data_dir)
        markdown = render_markdown(data_dir, context)
        context_dir = Path(config.get("GARMIN_CONTEXT_DIR") or data_dir / "context")
        context_dir.mkdir(parents=True, exist_ok=True)
        (context_dir / "summary.md").write_text(markdown, encoding="utf-8")
        mark("context")
    except Exception as error:
        print(f"  context error: {error}")
        mark("context", status="error", error=str(error))
    bar.advance("context")

    bar.finish()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
