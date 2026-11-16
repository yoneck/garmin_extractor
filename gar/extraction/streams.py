"""Per-activity time-series streams from ``get_activity_details``.

Garmin returns one JSON document per activity with two parallel arrays:
``metricDescriptors`` (index -> key, unit, factor) and
``activityDetailMetrics`` (one dict per sample with a ``metrics`` value list).

The raw payload is large (one sample per second), so this module keeps only the
metrics that matter for training analysis and downsamples long activities while
preserving every meaningful change. Full payloads belong in the raw archive.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Garmin metric key -> (our column, transform)
# ``None`` transform means "keep as-is". Units are verified against the live API:
# distance is already metres, elevation metres, speed m/s, temperatures Celsius.
METRIC_MAP: dict[str, str] = {
    "directTimestamp": "timestamp_ms",
    "sumElapsedDuration": "elapsed_s",
    "sumMovingDuration": "moving_s",
    "sumDistance": "distance_m",
    "directHeartRate": "heart_rate",
    "directSpeed": "speed_mps",
    "directGradeAdjustedSpeed": "gap_speed_mps",
    "directPower": "power_w",
    "sumAccumulatedPower": "accumulated_power_w",
    "directRunCadence": "run_cadence",
    "directDoubleCadence": "bike_cadence",
    "directElevation": "elevation_m",
    "directVerticalSpeed": "vertical_speed_mps",
    "directVerticalOscillation": "vertical_oscillation_cm",
    "directGroundContactTime": "ground_contact_ms",
    "directStrideLength": "stride_length_cm",
    "directVerticalRatio": "vertical_ratio",
    "directLatitude": "latitude",
    "directLongitude": "longitude",
    "directBodyBattery": "body_battery",
    "directPerformanceCondition": "performance_condition",
}

# Columns used to decide whether a sample is worth keeping when downsampling.
_KEY_COLUMNS = ("heart_rate", "power_w", "speed_mps", "elevation_m")


def _descriptor_index(descriptors: list) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for descriptor in descriptors or []:
        if not isinstance(descriptor, dict):
            continue
        index = descriptor.get("metricsIndex")
        key = descriptor.get("key")
        column = METRIC_MAP.get(key)
        if index is None or column is None:
            continue
        mapping[int(index)] = column
    return mapping


def _row_from_metrics(metrics: list, mapping: dict[int, str]) -> dict:
    row: dict = {}
    for index, column in mapping.items():
        if index >= len(metrics):
            continue
        value = metrics[index]
        if value is None:
            continue
        row[column] = value
    timestamp_ms = row.pop("timestamp_ms", None)
    if timestamp_ms:
        row["timestamp"] = int(float(timestamp_ms) / 1000)
    return row


def normalise(payload: dict) -> list[dict]:
    """Convert a Garmin details payload into flat rows with stable column names."""
    descriptors = payload.get("metricDescriptors") or []
    samples = payload.get("activityDetailMetrics") or []
    mapping = _descriptor_index(descriptors)
    rows: list[dict] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        metrics = sample.get("metrics")
        if not isinstance(metrics, list):
            continue
        row = _row_from_metrics(metrics, mapping)
        if row:
            rows.append(row)
    return rows


def downsample(
    rows: list[dict],
    min_interval_s: float = 5.0,
    max_points: int = 900,
) -> list[dict]:
    """Keep at most ``max_points`` rows, roughly ``min_interval_s`` apart.

    The first and last rows are always kept so totals stay correct. If the
    interval-based result is still too large, a uniform stride is applied.
    """
    if not rows:
        return []
    if len(rows) <= max_points and _spacing(rows) >= min_interval_s:
        return rows

    kept: list[dict] = [rows[0]]
    last_elapsed = rows[0].get("elapsed_s")
    for row in rows[1:-1]:
        elapsed = row.get("elapsed_s")
        if elapsed is None or last_elapsed is None:
            continue
        if elapsed - last_elapsed >= min_interval_s:
            kept.append(row)
            last_elapsed = elapsed
    if len(rows) > 1:
        last = rows[-1]
        previous_elapsed = kept[-1].get("elapsed_s") if kept else None
        if (
            len(kept) > 1
            and previous_elapsed is not None
            and last.get("elapsed_s") is not None
            and last["elapsed_s"] - previous_elapsed < min_interval_s
        ):
            kept.pop()
        kept.append(last)

    if len(kept) > max_points:
        stride = len(kept) / max_points
        kept = [kept[int(i * stride)] for i in range(max_points)]
        if kept[-1] is not rows[-1]:
            kept[-1] = rows[-1]
    return kept


def _spacing(rows: list[dict]) -> float:
    elapsed = [row.get("elapsed_s") for row in rows if row.get("elapsed_s") is not None]
    if len(elapsed) < 2:
        return float("inf")
    return (elapsed[-1] - elapsed[0]) / (len(elapsed) - 1)


def stream_stats(rows: list[dict], activity_id: str) -> dict:
    """Aggregate a stream into one summary row."""
    def values(column: str) -> list[float]:
        return [
            float(row[column])
            for row in rows
            if isinstance(row.get(column), (int, float))
        ]

    hr = values("heart_rate")
    power = values("power_w")
    speed = values("speed_mps")
    cadence = values("run_cadence") or values("bike_cadence")
    elevation = values("elevation_m")
    first_ts = next((row["timestamp"] for row in rows if row.get("timestamp")), 0)
    last_ts = next(
        (row["timestamp"] for row in reversed(rows) if row.get("timestamp")), first_ts
    )

    def avg(items: list[float]) -> float | None:
        return round(sum(items) / len(items), 2) if items else None

    return {
        "activityId": activity_id,
        "timestamp": first_ts,
        "endTimestamp": last_ts,
        "sampleCount": len(rows),
        "durationSeconds": int(last_ts - first_ts) if first_ts and last_ts else None,
        "avgHeartRate": avg(hr),
        "maxHeartRate": max(hr) if hr else None,
        "avgPower": avg(power),
        "maxPower": max(power) if power else None,
        "avgSpeedMps": avg(speed),
        "maxSpeedMps": max(speed) if speed else None,
        "avgCadence": avg(cadence),
        "maxCadence": max(cadence) if cadence else None,
        "elevationGain": _elevation_gain(elevation),
        "elevationLoss": _elevation_loss(elevation),
    }


def _elevation_gain(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(sum(max(0.0, b - a) for a, b in zip(values, values[1:])), 1)


def _elevation_loss(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(sum(max(0.0, a - b) for a, b in zip(values, values[1:])), 1)


def extract_activity_streams(
    client,
    storage,
    activity_id: str,
    min_interval_s: float = 5.0,
    max_points: int = 900,
    fetcher=None,
    today: datetime | None = None,
) -> dict:
    """Fetch, normalise, downsample and store one activity's stream.

    Returns the summary row written to ``activity_stream_stats``.
    """
    activity_id = str(activity_id)
    try:
        if fetcher is not None:
            payload = fetcher.call(
                "activity_details", "get_activity_details", activity_id
            )
        else:
            payload = client.get_activity_details(activity_id)
    except Exception as error:
        print(f"  stream error for {activity_id}: {error}")
        return {}
    if not isinstance(payload, dict):
        return {}
    rows = normalise(payload)
    if not rows:
        print(f"  no stream samples for {activity_id}.")
        return {}

    # Guard against future timestamps from a misconfigured device clock.
    limit = int((today or datetime.now(timezone.utc)).timestamp()) + 86400
    rows = [row for row in rows if not row.get("timestamp") or row["timestamp"] <= limit]
    if not rows:
        return {}

    kept = downsample(rows, min_interval_s=min_interval_s, max_points=max_points)
    payload_rows = []
    for index, row in enumerate(kept):
        record = dict(row)
        record["activityId"] = activity_id
        record["sampleIndex"] = index
        payload_rows.append(record)
    storage.sync_id("activity_streams", payload_rows)

    stats = stream_stats(rows, activity_id)
    storage.sync_id("activity_stream_stats", [stats])
    print(
        f"  stream {activity_id}: {len(rows)} samples -> {len(kept)} kept "
        f"(HR avg {stats.get('avgHeartRate')}, power avg {stats.get('avgPower')})"
    )
    return stats
