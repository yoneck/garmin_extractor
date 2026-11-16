"""Physiology metrics: HRV, stress, body battery, body composition."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value) -> int:
    """Normalize a date field to a unix timestamp int.

    Handles datetime/date objects, ISO datetimes ("2026-09-09T22:55:16.0"),
    ISO dates ("2026-09-10"), raw millisecond timestamps and ``YYYYMMDD``
    integers. Returns 0 when the value is unusable.
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number <= 0:
            return 0
        return int(number) if number < 10_000_000_000 else int(number / 1000)
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())
    text = str(value).strip()
    if not text:
        return 0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        pass
    if text.isdigit():
        if len(text) == 8:
            try:
                year, month, day = int(text[0:4]), int(text[4:6]), int(text[6:8])
                return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())
            except ValueError:
                return 0
        n = int(text)
        return n if n < 10_000_000_000 else int(n / 1000)
    return 0


def _days(start_date: datetime, end_date: datetime):
    today = date.today()
    current = start_date.date() if isinstance(start_date, datetime) else start_date
    last = end_date.date() if isinstance(end_date, datetime) else end_date
    if last > today:
        last = today
    while current <= last:
        yield current
        current += timedelta(days=1)


def _pairs(values) -> list[tuple[int, float]]:
    """Normalize ``[ts, value]`` / ``{timestamp, value}`` series."""
    out: list[tuple[int, float]] = []
    for item in values or []:
        ts = 0
        value = None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            ts = parse_ts(item[0])
            value = item[1]
        elif isinstance(item, dict):
            ts = parse_ts(
                item.get("timestamp")
                or item.get("startGMT")
                or item.get("ts")
                or item.get("time")
            )
            value = item.get("value")
            if value is None:
                value = item.get("bodyBatteryValue")
            if value is None:
                value = item.get("stressValue")
        if not ts or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        out.append((ts, number))
    return out


# ---------------------------------------------------------------------------
# HRV
# ---------------------------------------------------------------------------

def extract_hrv(client, storage, start_date: datetime, end_date: datetime):
    readings_count = 0
    nights = 0
    for day in _days(start_date, end_date):
        date_str = day.strftime("%Y-%m-%d")
        try:
            data = client.get_hrv_data(date_str) or {}
        except Exception as error:
            print(f"  HRV error on {date_str}: {error}")
            continue
        summary = data.get("hrvSummary") or {}
        readings = data.get("hrvReadings") or []
        values = [
            float(reading["hrvValue"])
            for reading in readings
            if isinstance(reading, dict) and reading.get("hrvValue") is not None
        ]
        night_mean = (
            summary.get("lastNightAvg")
            or data.get("avgOvernightHrv")
            or (sum(values) / len(values) if values else None)
        )
        status = summary.get("status") or data.get("hrvStatus")
        weekly_avg = summary.get("weeklyAvg")
        last_night_avg = summary.get("lastNightAvg") or data.get("avgOvernightHrv")
        day_ts = parse_ts(data.get("calendarDate") or date_str)
        feedback = summary.get("feedbackPhrase")

        if not values and night_mean is None and not status:
            continue

        nightly = {
            "timestamp": day_ts,
            "hrvDate": day_ts,
            "calendarDate": data.get("calendarDate") or date_str,
            "hrvNightMean": night_mean,
            "hrvMean": night_mean,
            "hrvStatus": status,
            "hrvWeeklyAvg": weekly_avg,
            "hrvLastNightAvg": last_night_avg,
            "hrvLastNight5MinHigh": summary.get("lastNight5MinHigh"),
            "hrvFeedback": feedback,
            "hrvReadingCount": len(values),
        }
        storage.sync("hrv_nightly", [nightly])
        nights += 1

        records = []
        for reading in readings:
            if not isinstance(reading, dict):
                continue
            ts = parse_ts(reading.get("readingTimeGMT") or reading.get("readingTimeLocal"))
            value = _to_float(reading.get("hrvValue"))
            if not ts or value is None:
                continue
            records.append(
                {
                    "timestamp": ts,
                    "hrvDate": day_ts,
                    "calendarDate": nightly["calendarDate"],
                    "hrvValue": value,
                    "hrvMean": night_mean,
                    "hrvStatus": status,
                    "hrvWeeklyAvg": weekly_avg,
                    "hrvLastNightAvg": last_night_avg,
                }
            )
        if records:
            storage.sync("hrv", records)
            readings_count += len(records)
    print(f"  written {readings_count} HRV readings across {nights} nights.")


# ---------------------------------------------------------------------------
# Stress
# ---------------------------------------------------------------------------

def extract_stress(client, storage, start_date: datetime, end_date: datetime):
    days = 0
    samples_written = 0
    for day in _days(start_date, end_date):
        date_str = day.strftime("%Y-%m-%d")
        try:
            data = client.get_stress_data(date_str) or {}
        except Exception as error:
            print(f"  stress error on {date_str}: {error}")
            continue
        calendar_date = data.get("calendarDate") or date_str
        pairs = _pairs(data.get("stressValuesArray"))
        samples = [(ts, value) for ts, value in pairs if value >= 0]
        if not samples:
            continue
        values = [value for _, value in samples]
        records = [
            {
                "timestamp": ts,
                "stressValue": int(value),
                "calendarDate": calendar_date,
            }
            for ts, value in samples
        ]
        storage.sync("stress_samples", records)
        samples_written += len(records)

        summary = {
            "timestamp": parse_ts(calendar_date),
            "stressDate": calendar_date,
            "averageStress": sum(values) / len(values),
            "minStress": min(values),
            "maxStress": max(values),
            "sampleCount": len(values),
            "restStressSeconds": data.get("restStressDuration"),
            "lowStressSeconds": data.get("lowStressDuration"),
            "mediumStressSeconds": data.get("mediumStressDuration"),
            "highStressSeconds": data.get("highStressDuration"),
            "veryHighStressSeconds": data.get("activityStressDuration")
            or data.get("veryHighStressDuration"),
            "stressDurationSeconds": data.get("stressDuration"),
            "stressQualifier": data.get("stressQualifier"),
        }
        storage.sync("stress", [summary])
        days += 1
    print(f"  written {samples_written} stress samples across {days} days.")


# ---------------------------------------------------------------------------
# Body Battery
# ---------------------------------------------------------------------------

def extract_body_battery(client, storage, start_date: datetime, end_date: datetime):
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = min(end_date, datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    try:
        rows = client.get_body_battery(start_str, end_str) or []
    except Exception as error:
        print(f"  body battery error: {error}")
        return
    days = 0
    samples_written = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        calendar_date = row.get("date") or row.get("calendarDate")
        pairs = _pairs(row.get("bodyBatteryValuesArray"))
        if not pairs:
            continue
        timestamp = (
            parse_ts(row.get("startTimestampGMT"))
            or parse_ts(calendar_date)
            or pairs[0][0]
        )
        if not timestamp:
            continue
        samples = [
            {
                "timestamp": ts,
                "bodyBatteryValue": int(value),
                "calendarDate": calendar_date,
            }
            for ts, value in pairs
        ]
        storage.sync("body_battery_samples", samples)
        samples_written += len(samples)
        numbers = [value for _, value in pairs]
        storage.sync(
            "body_battery",
            [
                {
                    "timestamp": timestamp,
                    "calendarDate": calendar_date,
                    "charged": row.get("charged"),
                    "drained": row.get("drained"),
                    "bodyBatteryOpen": numbers[0],
                    "bodyBatteryClose": numbers[-1],
                    "bodyBatteryMean": sum(numbers) / len(numbers),
                    "bodyBatteryMin": min(numbers),
                    "bodyBatteryMax": max(numbers),
                    "sampleCount": len(numbers),
                }
            ],
        )
        days += 1
    print(f"  written {samples_written} body battery samples across {days} days.")


# ---------------------------------------------------------------------------
# Body composition
# ---------------------------------------------------------------------------

def extract_body_composition(client, storage, start_date: datetime, end_date: datetime):
    end_str = min(end_date, datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    try:
        data = client.get_body_composition(
            start_date.strftime("%Y-%m-%d"), end_str
        )
    except Exception as error:
        print(f"  body composition error: {error}")
        return
    rows = (data or {}).get("dateWeightList") or []
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = parse_ts(row.get("timestampGMT")) or parse_ts(row.get("date"))
        if not timestamp:
            continue
        weight_kg = (
            _to_float(row.get("weight")) / 1000
            if row.get("weight") is not None
            else None
        )
        records.append(
            {
                "timestamp": timestamp,
                "calendarDate": row.get("calendarDate") or row.get("date"),
                "weightKg": weight_kg,
                "bmi": _to_float(row.get("bmi")),
                "bodyFatPct": _to_float(row.get("bodyFat")),
                "bodyWaterPct": _to_float(row.get("bodyWater")),
                "boneMassKg": _to_float(row.get("boneMass")) / 1000
                if row.get("boneMass") is not None
                else None,
                "muscleMassKg": _to_float(row.get("muscleMass")) / 1000
                if row.get("muscleMass") is not None
                else None,
                "bodyFatKg": _to_float(row.get("bodyFatMass")) / 1000
                if row.get("bodyFatMass") is not None
                else None,
                "visceralFat": _to_float(row.get("visceralFat")),
                "bodyScore": _to_float(row.get("bodyScore")),
                "physiqueRating": _to_float(row.get("physiqueRating")),
                "metabolicAge": _to_float(row.get("metabolicAge")),
                "sourceType": row.get("sourceType"),
            }
        )
    if records:
        storage.sync("body_composition", records)
    print(f"  written {len(records)} body composition records.")
