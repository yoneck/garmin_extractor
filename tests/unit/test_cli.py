"""CLI subcommand tests that do not touch the network."""
import json
from pathlib import Path

from gar.cli.main import main
from gar.utils.storage import CsvStorage


def test_coverage_subcommand_renders_and_writes(tmp_path: Path, capsys):
    storage = CsvStorage(tmp_path)
    storage.sync(
        "daily_summary",
        [{"timestamp": 1794771363, "date": "2026-11-15", "totalSteps": 1000}],
    )
    out_file = tmp_path / "coverage.md"
    rc = main(["coverage", "--data-dir", str(tmp_path), "--days", "7", "--out", str(out_file)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "Data coverage" in text
    assert "daily_summary" in text
    assert out_file.exists()


def test_coverage_subcommand_json(tmp_path: Path, capsys):
    rc = main(["coverage", "--data-dir", str(tmp_path), "--days", "7", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["window"]["days"] == 7
    assert report["datasets"]
