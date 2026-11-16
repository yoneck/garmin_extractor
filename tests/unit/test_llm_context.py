"""Context summary contract and coverage tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gar.context.llm_context import generate_context, render_markdown
from gar.utils.storage import CsvStorage


def _ts(days_ago=0):
    return int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())


def test_summary_uses_real_column_names(tmp_path: Path):
    storage = CsvStorage(tmp_path)
    storage.sync(
        "activities",
        [
            {"activityId": "1", "timestamp": _ts(1), "sportType": "CYCLING",
             "distanceMeter": 50000, "durationSecond": 5400, "trainingLoad": 100},
            {"activityId": "2", "timestamp": _ts(2), "sportType": "RUNNING",
             "distanceMeter": 10000, "durationSecond": 3000, "trainingLoad": 50},
        ],
    )
    storage.sync(
        "sleep_summary",
        [{"timestamp": _ts(i), "calendarDate": f"2026-11-{10 + i}",
          "sleepTimeSeconds": 25200 + i * 60, "overallSleepScore": 70 + i,
          "deepSleepSeconds": 3600, "remSleepSeconds": 5400}
         for i in range(3)],
    )
    storage.sync(
        "hrv_nightly",
        [{"timestamp": _ts(i), "hrvDate": _ts(i), "hrvNightMean": 40 + i,
          "hrvStatus": "BALANCED"} for i in range(3)],
    )
    storage.sync(
        "stress",
        [{"timestamp": _ts(i), "averageStress": 30 + i, "maxStress": 60 + i}
         for i in range(3)],
    )
    storage.sync(
        "body_battery",
        [{"timestamp": _ts(i), "bodyBatteryOpen": 50 + i,
          "bodyBatteryClose": 30 + i, "bodyBatteryMean": 40 + i}
         for i in range(3)],
    )
    storage.sync(
        "body_composition",
        [{"timestamp": _ts(10), "weightKg": 88.0},
         {"timestamp": _ts(1), "weightKg": 87.0}],
    )

    context = generate_context(tmp_path)
    content = context["content"]
    assert "Avg sleep (min)" in content
    assert "Avg sleep score" in content
    assert "Avg nightly HRV" in content
    assert "Avg end-of-day" in content
    assert "Average weight" in content
    assert context["coverage"]["datasets"]
    markdown = render_markdown(tmp_path, context)
    assert "# Garmin Data Summary" in markdown
    assert "Data coverage" in markdown


def test_empty_context(tmp_path: Path):
    context = generate_context(tmp_path)
    assert context["empty"] is True
