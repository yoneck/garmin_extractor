"""Subjective log tests (validation, storage and CLI)."""
import json
from datetime import date
from pathlib import Path

import pandas as pd

from gar.input.subjective import build_entry, load_entries, main, save_entry


def test_build_entry_validates_and_serialises():
    entry = build_entry(
        date(2026, 11, 16),
        {"sleep": 3, "fatigue": 4, "soreness": None, "motivation": 2,
         "stress": 3, "mood": 3},
        note="Travel day",
        tags=["travel", "travel", "alcohol"],
        rpe=7,
        sleep_hours=5.5,
        weight_kg=87.2,
    )
    assert entry["date"] == "2026-11-16"
    assert entry["sleepQuality"] == 3
    assert entry["fatigue"] == 4
    assert entry["hasAnyRating"] is True
    assert json.loads(entry["tags"]) == ["alcohol", "travel"]
    assert entry["timestamp"] > 0


def test_build_entry_rejects_out_of_range():
    for field, value in (("sleep", 0), ("mood", 6)):
        try:
            build_entry(date(2026, 11, 16), {field: value})
        except ValueError as error:
            assert field in str(error)
        else:  # pragma: no cover
            raise AssertionError(f"{field}={value} should fail")
    try:
        build_entry(date(2026, 11, 16), {}, rpe=11)
    except ValueError as error:
        assert "rpe" in str(error)
    else:  # pragma: no cover
        raise AssertionError("rpe=11 should fail")


def test_save_is_idempotent_per_date(tmp_path: Path):
    first = build_entry(date(2026, 11, 15), {"sleep": 2}, note="bad night")
    save_entry(tmp_path, first)
    second = build_entry(date(2026, 11, 15), {"sleep": 4}, note="better")
    save_entry(tmp_path, second)
    frame = pd.read_csv(tmp_path / "subjective" / "subjective.csv")
    assert len(frame) == 1
    assert frame.iloc[0]["sleepQuality"] == 4
    assert frame.iloc[0]["note"] == "better"


def test_cli_writes_and_reads(tmp_path: Path, capsys):
    rc = main(
        [
            "--date", "2026-11-16",
            "--sleep", "3",
            "--fatigue", "4",
            "--note", "Travel",
            "--tags", "travel",
        ],
        data_dir=tmp_path,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "2026-11-16" in out and "fatigue=4" in out

    entries = load_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["note"] == "Travel"

    rc = main(["--show", "--json"], data_dir=tmp_path)
    assert rc == 0
    printed = capsys.readouterr().out
    assert json.loads(printed)[0]["date"] == "2026-11-16"


def test_cli_requires_some_content(tmp_path: Path, capsys):
    rc = main(["--date", "2026-11-16"], data_dir=tmp_path)
    assert rc == 2
    assert "at least one rating" in capsys.readouterr().out


def test_cli_rejects_bad_rating(tmp_path: Path, capsys):
    rc = main(["--sleep", "9"], data_dir=tmp_path)
    assert rc == 2
    assert "between 1 and 5" in capsys.readouterr().out
