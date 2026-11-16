"""Layered LLM context builder (M3).

The builder reads the DuckDB lake (built by :mod:`gar.lake`) and writes a small
set of focused markdown files plus a combined ``context.md``. Each file is
bounded by a token budget so that the whole package fits a 5-15k token prompt.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from gar.lake.db import connect, lake_path, table_exists
from gar.lake.ingest import discover_datasets, ingest_all
from gar.lake.derive import derive_all

TOKEN_CHAR_RATIO = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // TOKEN_CHAR_RATIO)


def _ensure_lake(data_dir: Path) -> duckdb.DuckDBPyConnection:
    path = lake_path(data_dir)
    con = connect(data_dir)
    if table_exists(con, "fact_daily") and con.execute(
        "SELECT count(*) FROM fact_daily"
    ).fetchall()[0][0] > 0:
        return con
    # Bootstrap from whatever CSVs are present.
    if discover_datasets(data_dir):
        ingest_all(con, data_dir, verbose=False)
        derive_all(con, verbose=False)
    return con


def _query(con, sql: str) -> list[dict]:
    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception:  # noqa: BLE001
        return []


def _fmt(value: Any, digits: int = 0) -> str:
    if value is None:
        return "-"
    try:
        if isinstance(value, float):
            if math.isnan(value):
                return "-"
            return f"{value:.{digits}f}"
        return str(value)
    except Exception:  # noqa: BLE001
        return str(value)


def _date(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)[:10]


@dataclass
class ContextSection:
    name: str
    title: str
    body: str
    priority: int = 5

    @property
    def tokens(self) -> int:
        return _estimate_tokens(self.body)


@dataclass
class ContextPackage:
    data_dir: Path
    days: int
    token_budget: int
    sections: list[ContextSection] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"# Garmin training context ({self.days} days)", ""]
        for section in self.sections:
            lines.append(section.body)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def total_tokens(self) -> int:
        return sum(section.tokens for section in self.sections)


def _profile_section(con, data_dir: Path) -> ContextSection:
    thresholds = _query(
        con,
        "SELECT kind, sport, value, unit FROM fact_threshold ORDER BY kind",
    )
    zones = _query(
        con,
        "SELECT sport, zone_type, zone, low_boundary, high_boundary "
        "FROM fact_zone_def ORDER BY sport, zone_type, zone",
    )
    gear = _query(con, "SELECT name, gear_type, max_distance_m FROM fact_gear")
    lines = ["## Profile"]
    if thresholds:
        lines.append("### Thresholds")
        for row in thresholds:
            lines.append(
                f"- {row['kind']} ({row.get('sport') or '-'}): "
                f"{_fmt(row['value'], 1)} {row.get('unit') or ''}"
            )
    if zones:
        lines.append("### Zones")
        for row in zones[:40]:
            lines.append(
                f"- {row.get('sport')} {row.get('zone_type')} Z{row['zone']}: "
                f"{_fmt(row.get('low_boundary'), 0)}-{_fmt(row.get('high_boundary'), 0)}"
            )
    if gear:
        lines.append("### Gear")
        for row in gear:
            lines.append(
                f"- {row.get('name')} ({row.get('gear_type')}) max "
                f"{_fmt((row.get('max_distance_m') or 0) / 1000, 0)} km"
            )
    return ContextSection("profile", "Profile", "\n".join(lines), priority=1)


def _daily_section(con, days: int, token_budget: int) -> ContextSection:
    rows = _query(
        con,
        f"""
        SELECT * FROM fact_daily
        ORDER BY date_local DESC
        LIMIT {max(1, days)}
        """,
    )
    rows = list(reversed(rows))
    lines = [
        "## Daily (one row per day)",
        "date | ready | sleep_min(score) | HRV_ms(status,z) | RHR_bpm(Δ) | "
        "BB_wake/end | stress_avg | load | ACWR | flags",
    ]
    for row in rows:
        date_value = _date(row.get("date_local"))
        sleep = f"{_fmt(row.get('sleep_total_min'), 0)}({_fmt(row.get('sleep_score'), 0)})"
        hrv = (
            f"{_fmt(row.get('hrv_night_avg_ms'), 0)}"
            f"({_fmt(row.get('hrv_status'))},{_fmt(row.get('hrv_zscore'), 1)})"
        )
        rhr = f"{_fmt(row.get('rhr_bpm'), 0)}({_fmt(row.get('rhr_delta_bpm'), 1)})"
        bb = (
            f"{_fmt(row.get('body_battery_wake'), 0)}/"
            f"{_fmt(row.get('body_battery_end_of_day'), 0)}"
        )
        lines.append(
            " | ".join(
                [
                    date_value,
                    _fmt(row.get("readiness_score"), 0),
                    sleep,
                    hrv,
                    rhr,
                    bb,
                    _fmt(row.get("stress_avg"), 0),
                    _fmt(row.get("training_load_day"), 0),
                    _fmt(row.get("acwr"), 2),
                    _fmt(row.get("sleep_debt_7d_min"), 0) + "m debt",
                ]
            )
        )
    body = "\n".join(lines)
    # Downsample if over budget.
    while _estimate_tokens(body) > token_budget and len(rows) > 7:
        rows = rows[::2]
        lines = lines[:2] + [
            " | ".join(
                [
                    _date(row.get("date_local")),
                    _fmt(row.get("readiness_score"), 0),
                    f"{_fmt(row.get('sleep_total_min'), 0)}({_fmt(row.get('sleep_score'), 0)})",
                    f"{_fmt(row.get('hrv_night_avg_ms'), 0)}({_fmt(row.get('hrv_status'))})",
                    _fmt(row.get("rhr_bpm"), 0),
                    f"{_fmt(row.get('body_battery_wake'),0)}/{_fmt(row.get('body_battery_end_of_day'),0)}",
                    _fmt(row.get("stress_avg"), 0),
                    _fmt(row.get("training_load_day"), 0),
                    _fmt(row.get("acwr"), 2),
                    _fmt(row.get("sleep_debt_7d_min"), 0) + "m",
                ]
            )
            for row in rows
        ]
        body = "\n".join(lines)
    return ContextSection("daily", "Daily", body, priority=2)


def _activities_section(con, days: int) -> ContextSection:
    rows = _query(
        con,
        f"""
        SELECT activity_id, start_utc, sport_type, name, duration_s,
               moving_duration_s, distance_m, avg_hr_bpm, max_hr_bpm,
               avg_power_w, np_w, training_load, tss, if_, efficiency_factor,
               trimp, calories, aerobic_te, anaerobic_te
        FROM fact_activity
        ORDER BY start_utc DESC
        LIMIT 25
        """,
    )
    lines = ["## Recent activities (newest first)"]
    for row in rows:
        lines.append(
            f"- {_date(row.get('start_utc'))} {row.get('sport_type')} "
            f"{row.get('name') or ''}: "
            f"{_fmt((row.get('distance_m') or 0)/1000, 1)} km, "
            f"{_fmt((row.get('moving_duration_s') or 0)/60, 0)} min, "
            f"HR {_fmt(row.get('avg_hr_bpm'), 0)}/{_fmt(row.get('max_hr_bpm'), 0)}, "
            f"power {_fmt(row.get('avg_power_w'), 0)}/{_fmt(row.get('np_w'), 0)} W, "
            f"load {_fmt(row.get('training_load'), 0)}, TSS {_fmt(row.get('tss'), 0)}, "
            f"TE {_fmt(row.get('aerobic_te'), 1)}/{_fmt(row.get('anaerobic_te'), 1)}"
        )
    return ContextSection("activities", "Activities", "\n".join(lines), priority=3)


def _physiology_section(con) -> ContextSection:
    sleep = _query(
        con,
        """
        SELECT date_local, sleep_total_min, deep_min, rem_min, light_min,
               awake_min, sleep_efficiency_pct, sleep_score, avg_hrv_ms, hrv_status
        FROM fact_sleep_session ORDER BY date_local DESC LIMIT 14
        """,
    )
    lines = ["## Sleep detail (last 14 nights)"]
    for row in sleep:
        lines.append(
            f"- {_date(row.get('date_local'))}: "
            f"{_fmt(row.get('sleep_total_min'), 0)} min "
            f"(deep {_fmt(row.get('deep_min'), 0)}, rem {_fmt(row.get('rem_min'), 0)}, "
            f"light {_fmt(row.get('light_min'), 0)}, awake {_fmt(row.get('awake_min'), 0)}), "
            f"eff {_fmt(row.get('sleep_efficiency_pct'), 0)}%, score {_fmt(row.get('sleep_score'), 0)}, "
            f"HRV {_fmt(row.get('avg_hrv_ms'), 0)} ms ({_fmt(row.get('hrv_status'))})"
        )
    hrv = _query(
        con,
        """
        SELECT date_local, hrv_night_avg_ms, hrv_weekly_avg_ms, hrv_status
        FROM fact_daily WHERE hrv_night_avg_ms IS NOT NULL
        ORDER BY date_local DESC LIMIT 14
        """,
    )
    if hrv:
        lines.append("## HRV trend (last 14 nights)")
        for row in hrv:
            lines.append(
                f"- {_date(row.get('date_local'))}: "
                f"{_fmt(row.get('hrv_night_avg_ms'), 0)} ms "
                f"(weekly {_fmt(row.get('hrv_weekly_avg_ms'), 0)}, {_fmt(row.get('hrv_status'))})"
            )
    return ContextSection("physiology", "Physiology", "\n".join(lines), priority=4)


def _plan_section(con) -> ContextSection:
    adherence = _query(
        con,
        """
        SELECT date_local, title, sport, status, matched_activity_id
        FROM fact_workout_adherence
        ORDER BY date_local DESC LIMIT 30
        """,
    )
    weather = _query(
        con,
        """
        SELECT a.activity_id, w.temp_c, w.humidity_pct, w.wind_speed_kmh, w.condition
        FROM fact_weather w JOIN fact_activity a ON a.activity_id = w.activity_id
        ORDER BY a.start_utc DESC LIMIT 15
        """,
    )
    lines = ["## Plan adherence (recent)"]
    for row in adherence:
        lines.append(
            f"- {_date(row.get('date_local'))} {row.get('sport')}: "
            f"{row.get('title')} -> {row.get('status')}"
        )
    if weather:
        lines.append("## Weather during recent activities")
        for row in weather:
            lines.append(
                f"- activity {row.get('activity_id')}: "
                f"{_fmt(row.get('temp_c'), 1)} C, RH {_fmt(row.get('humidity_pct'), 0)}%, "
                f"wind {_fmt(row.get('wind_speed_kmh'), 1)} km/h, {row.get('condition')}"
            )
    return ContextSection("plan", "Plan", "\n".join(lines), priority=5)


def _subjective_section(con) -> ContextSection:
    rows = _query(
        con,
        """
        SELECT date_local, rpe, mood, soreness, sleep_feel, fatigue,
               stress_note, notes
        FROM fact_subjective
        ORDER BY date_local DESC LIMIT 20
        """,
    )
    lines = ["## Subjective log"]
    for row in rows:
        bits = []
        for key, label in (
            ("rpe", "rpe"),
            ("mood", "mood"),
            ("soreness", "soreness"),
            ("sleep_feel", "sleep"),
            ("fatigue", "fatigue"),
        ):
            value = row.get(key)
            if value is not None:
                bits.append(f"{label}={_fmt(value, 0)}")
        note = row.get("stress_note")
        if note:
            bits.append(f"note=\"{note}\"")
        lines.append(f"- {_date(row.get('date_local'))}: " + ", ".join(bits))
    return ContextSection("subjective", "Subjective", "\n".join(lines), priority=6)


def _flags_section(con) -> ContextSection:
    rows = _query(
        con,
        """
        SELECT date_local, severity, code, value, threshold, reason
        FROM fact_flag
        ORDER BY date_local DESC LIMIT 40
        """,
    )
    lines = ["## Flags"]
    for row in rows:
        lines.append(
            f"- {_date(row.get('date_local'))} [{row.get('severity')}] "
            f"{row.get('code')}: {_fmt(row.get('value'), 2)} "
            f"(thr {_fmt(row.get('threshold'), 2)}) - {row.get('reason')}"
        )
    return ContextSection("flags", "Flags", "\n".join(lines), priority=7)


def _coverage_section(con, data_dir: Path, days: int) -> ContextSection:
    from gar.context.coverage import coverage_report, render_coverage

    report = coverage_report(data_dir, days=days)
    text = render_coverage(report)
    return ContextSection("coverage", "Coverage", text, priority=8)


def build_context(
    data_dir: Path,
    days: int = 28,
    token_budget: int = 12000,
    out_dir: Path | None = None,
) -> ContextPackage:
    data_dir = Path(data_dir)
    out_dir = Path(out_dir) if out_dir else data_dir / "context"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = _ensure_lake(data_dir)
    try:
        package = ContextPackage(data_dir=data_dir, days=days, token_budget=token_budget)
        package.sections.append(_profile_section(con, data_dir))
        package.sections.append(_daily_section(con, days, token_budget // 3))
        package.sections.append(_activities_section(con, days))
        package.sections.append(_physiology_section(con))
        package.sections.append(_plan_section(con))
        package.sections.append(_subjective_section(con))
        package.sections.append(_flags_section(con))
        package.sections.append(_coverage_section(con, data_dir, days))

        # Trim to budget by dropping lowest-priority sections last.
        while package.total_tokens() > token_budget and len(package.sections) > 4:
            # Drop the last section (lowest priority) unless it is tiny.
            package.sections.pop()

        # Write individual files.
        for section in package.sections:
            (out_dir / f"{section.name}.md").write_text(
                section.body + "\n", encoding="utf-8"
            )
        (out_dir / "context.md").write_text(package.render(), encoding="utf-8")
        manifest = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "token_budget": token_budget,
            "estimated_tokens": package.total_tokens(),
            "sections": [
                {"name": s.name, "tokens": s.tokens, "priority": s.priority}
                for s in package.sections
            ],
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return package
    finally:
        con.close()


# Data dictionary written once per build.
DATA_DICTIONARY = """# Data dictionary (lake)

| Table | Meaning | Key columns |
| --- | --- | --- |
| fact_daily | One row per day, merged sources | date_local, readiness_score, sleep_total_min, sleep_score, hrv_night_avg_ms, hrv_status, hrv_zscore, rhr_bpm, rhr_delta_bpm, body_battery_wake, body_battery_end_of_day, stress_avg, training_load_day, acwr, sleep_debt_7d_min, monotony, strain, load_7d_sum, load_ratio_7d_28d, intensity_low_min, intensity_high_min |
| fact_activity | One row per activity | activity_id, start_utc, sport_type, distance_m, moving_duration_s, avg_hr_bpm, max_hr_bpm, avg_power_w, np_w, training_load, tss, if_, efficiency_factor, trimp, time_in_zone_1_min..5 |
| fact_sleep_session | One row per night | date_local, sleep_total_min, deep_min, rem_min, light_min, awake_min, sleep_efficiency_pct, sleep_score, avg_hrv_ms, hrv_status, start_utc, end_utc |
| fact_flag | Data-quality / training flags | date_local, severity, code, value, threshold, reason |
| fact_threshold | FTP, lactate threshold, LTHR | date_local, kind, sport, value, unit |
| fact_zone_def | Power/HR zone floors | date_local, sport, zone_type, zone, low_boundary, high_boundary |
| fact_workout_adherence | Planned vs completed | date_local, title, sport, status, matched_activity_id |
| fact_subjective | Manual log | date_local, rpe, mood, soreness, sleep_feel, fatigue, stress_note |
| fact_weather | Conditions per activity | activity_id, temp_c, humidity_pct, wind_speed_kmh, condition |
"""


def write_data_dictionary(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data_dictionary.md").write_text(DATA_DICTIONARY, encoding="utf-8")
