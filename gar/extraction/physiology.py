"""Physiology metrics: HRV, stress, body battery, body composition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return _to_int(value) if value < 10_000_000_000 else _to_int(value / 1000)
    if isinstance(value, datetime):
        return _to_int(value.timestamp())
    text = str(value).strip()
    if not text:
        return 0
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return _to_int(dt.timestamp())
    except ValueError:
        pass
    if text.isdigit():
        n = int(text)
        if n < 10_000_000_000:
            return n
        return _to_int(n / 1000)
    if len(text) == 8:
        digits = text
        try:
            day, month, year = digits[2:4], digits[4:6], digits[6:]
            return _to_int(datetime(int(year), int(month), int(day)).timestamp())
        except Exception:
            return 0
    return 0


def _days(start_date: datetime, end_date: datetime):
    offset = 0
    max_days = (end_date - start_date).days + 2
    while offset <= max_days:
        yield start_date + timedelta(days=offset)
        offset += 1


def extract_hrv(client, storage, start_date: datetime, end_date: datetime):
    records = []
    for day in _days(start_date, end_date):
        date_str = day.strftime("%Y-%m-%d")
        try:
            data = client.get_hrv_data(date_str) or {}
        except Exception as error:
            print(f"  HRV error on {date_str}: {error}")
            continue
        readings = data.get("hrvReadings") or []
        daily_record = data.get("hrvSummary") or {}
        for reading in readings:
            if not isinstance(reading, dict):
                continue
            timestamp = parse_ts(reading.get("readingTimeGMT") or reading.get("readingTimeLocal"))
            if not timestamp:
                continue
            value = reading.get("hrvValue")
            if value is None:
                continue
            ts_day = parse_ts(reading.get("hrvDate"))
            records.append({
                "timestamp": timestamp,
                "hrvDate": ts_day or parse_ts(data.get("startTimestampGMT")),
                "hrvRMSSD": _to_float(value),
                "hrvMean": _to_float(value),
                "hrvStatus": daily_record.get("status"),
                "hrvWeeklyAvg": daily_record.get("weeklyAvg"),
                "hrvLastNightAvg": daily_record.get("lastNightAvg"),
            })
    if not records:
        print(f"  no HRV readings for range.")
        return
    storage.sync("hrv", records)
    print(f"  written HRV records ({len(records)}).")


def extract_stress(client, storage, start_date: datetime, end_date: datetime):
    for day in _days(start_date, end_date):
        date_str = day.strftime("%Y-%m-%d")
        try:
            data = client.get_stress_data(date_str) or {}
        except Exception as error:
            print(f"  stress error on {date_str}: {error}")
            continue
        values = data.get("stressValuesArray") or []
        samples = []
        for pair in values:
            if not isinstance(pair, list) or len(pair) < 2:
                continue
            try:
                value = int(pair[1])
            except (TypeError, ValueError):
                continue
            if value == -1:
                continue
            samples.append(value)
        if not samples:
            continue
        record = {
            "timestamp": parse_ts(data.get("calendarDate")),
            "stressDate": data.get("calendarDate"),
            "averageStress": sum(samples) / len(samples),
            "minStress": min(samples),
            "maxStress": max(samples),
        }
        storage.sync("stress", [record])
    print(f"  written stress records.")


def extract_body_battery(client, storage, start_date: datetime, end_date: datetime):
    records = []
    for day in _days(start_date, end_date):
        day_str = day.strftime("%Y-%m-%d")
        try:
            rows = client.get_body_battery(day_str, day_str) or []
        except Exception as error:
            print(f"  body battery error on {day_str}: {error}")
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get("bodyBatteryValuesArray") or []
            numbers = []
            for pair in value:
                if isinstance(pair, list) and len(pair) > 1:
                    try:
                        numbers.append(int(pair[1]))
                    except (TypeError, ValueError):
                        pass
            if not numbers or row.get("charged") is None:
                continue
            timestamp = parse_ts(row.get("date")) or parse_ts((value[0][0] if value else None))
            records.append({
                "timestamp": timestamp,
                "charged": row.get("charged"),
                "drained": row.get("drained"),
                "bodyBatteryOpen": numbers[0],
                "bodyBatteryClose": numbers[-1],
                "bodyBatteryMean": sum(numbers) / len(numbers),
            })
    if records:
        storage.sync("body_battery", records)
    print(f"  written {len(records)} body battery records.")


def extract_body_composition(client, storage, start_date: datetime, end_date: datetime):
    try:
        data = client.get_body_composition(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
    except Exception as error:
        print(f"  body composition error: {error}")
        return
    rows = (data or {}).get("dateWeightList") or []
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = parse_ts(row.get("date")) or parse_ts(row.get("timestampGMT"))
        if not timestamp:
            continue
        weight_kg = _to_float(row.get("weight")) / 1000 if row.get("weight") is not None else None
        records.append({
            "timestamp": timestamp,
            "calendarDate": row.get("calendarDate"),
            "weightKg": weight_kg,
            "bmi": _to_float(row.get("bmi")),
            "bodyFatPct": _to_float(row.get("bodyFat")),
            "bodyWaterPct": _to_float(row.get("bodyWater")),
            "boneMassKg": _to_float(row.get("boneMass")) / 1000 if row.get("boneMass") is not None else None,
            "muscleMassKg": _to_float(row.get("muscleMass")) / 1000 if row.get("muscleMass") is not None else None,
            "bodyFatKg": _to_float(row.get("bodyFatMass")) / 1000 if row.get("bodyFatMass") is not None else None,
            "bodyScore": _to_float(row.get("bodyScore")),
            "physiqueRating": _to_float(row.get("physiqueRating")),
            "metabolicAge": _to_float(row.get("metabolicAge")),
            "sourceType": row.get("sourceType"),
        })
    if records:
        storage.sync("body_composition", records)
    print(f"  written {len(records)} body composition records.")
