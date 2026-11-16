"""Activity stream tests (fake details payload, no network)."""
from pathlib import Path

import pandas as pd

from gar.extraction.streams import (
    downsample,
    extract_activity_streams,
    normalise,
    stream_stats,
)
from gar.utils.storage import CsvStorage


def _payload(samples: int = 60) -> dict:
    descriptors = [
        {"metricsIndex": 0, "key": "directTimestamp", "unit": {"key": "gmt"}},
        {"metricsIndex": 1, "key": "sumElapsedDuration", "unit": {"key": "second"}},
        {"metricsIndex": 2, "key": "sumDistance", "unit": {"key": "meter"}},
        {"metricsIndex": 3, "key": "directHeartRate", "unit": {"key": "bpm"}},
        {"metricsIndex": 4, "key": "directPower", "unit": {"key": "watt"}},
        {"metricsIndex": 5, "key": "directElevation", "unit": {"key": "meter"}},
        {"metricsIndex": 6, "key": "directSpeed", "unit": {"key": "mps"}},
        {"metricsIndex": 7, "key": "unknownMetric", "unit": {"key": "x"}},
    ]
    details = []
    start_ms = 1794771363000
    for second in range(samples):
        details.append(
            {
                "metrics": [
                    start_ms + second * 1000,
                    second,
                    second * 3.5,
                    120 + second,
                    200 + second,
                    83.0 + second * 0.1,
                    3.5,
                    999,
                ]
            }
        )
    return {"activityId": 1, "metricDescriptors": descriptors, "activityDetailMetrics": details}


def test_normalise_maps_known_metrics_only():
    rows = normalise(_payload(3))
    assert len(rows) == 3
    assert rows[0]["elapsed_s"] == 0
    assert rows[0]["heart_rate"] == 120
    assert rows[0]["power_w"] == 200
    assert rows[0]["timestamp"] > 0
    assert "unknownMetric" not in rows[0]


def test_downsample_respects_interval_and_keeps_edges():
    rows = normalise(_payload(120))
    kept = downsample(rows, min_interval_s=10, max_points=900)
    elapsed = [row["elapsed_s"] for row in kept]
    assert elapsed[0] == 0
    assert elapsed[-1] == 119
    assert all(b - a >= 10 for a, b in zip(elapsed, elapsed[1:]))


def test_downsample_caps_points():
    rows = normalise(_payload(600))
    kept = downsample(rows, min_interval_s=1, max_points=50)
    assert len(kept) <= 50
    assert kept[-1]["elapsed_s"] == 599


def test_stream_stats_aggregate():
    rows = normalise(_payload(60))
    stats = stream_stats(rows, "1")
    assert stats["sampleCount"] == 60
    assert stats["avgHeartRate"] == 149.5
    assert stats["maxHeartRate"] == 179
    assert stats["avgPower"] == 229.5
    assert stats["elevationGain"] > 0


def test_extract_streams_is_idempotent(tmp_path: Path):
    class StreamClient:
        def get_activity_details(self, activity_id):
            return _payload(60)

    storage = CsvStorage(tmp_path)
    stats = extract_activity_streams(StreamClient(), storage, "1", min_interval_s=10)
    assert stats["activityId"] == "1"
    extract_activity_streams(StreamClient(), storage, "1", min_interval_s=10)

    streams = pd.read_csv(tmp_path / "activity_streams" / "activity_streams.csv")
    assert len(streams) == len(
        {int(value) for value in streams["sampleIndex"]}
    )
    assert streams["activityId"].astype(str).nunique() == 1
    stats_df = pd.read_csv(tmp_path / "activity_stream_stats" / "activity_stream_stats.csv")
    assert len(stats_df) == 1
