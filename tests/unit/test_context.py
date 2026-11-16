"""Context builder tests (M3)."""
import json
from pathlib import Path

import pytest

from gar.context.builder import build_context


def test_build_context_writes_layered_files(tmp_path: Path):
    # Reuse the lake smoke fixture if present, otherwise skip.
    smoke = Path("/tmp/opencode/lake_smoke")
    if not (smoke / "garmin.duckdb").exists():
        pytest.skip("lake smoke fixture not available")
    pkg = build_context(smoke, days=7, token_budget=4000, out_dir=tmp_path / "context")
    assert pkg.total_tokens() <= 4000
    out = tmp_path / "context"
    for name in ("profile.md", "daily.md", "activities.md", "context.md", "manifest.json"):
        assert (out / name).exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["estimated_tokens"] == pkg.total_tokens()
    assert "daily" in manifest["sections"][1]["name"] or any(s["name"] == "daily" for s in manifest["sections"])
    text = (out / "context.md").read_text()
    assert "Garmin training context" in text
    assert "## Daily" in text


def test_build_context_handles_empty_lake(tmp_path: Path):
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    pkg = build_context(data_dir, days=3, token_budget=2000, out_dir=data_dir / "context")
    assert (data_dir / "context" / "context.md").exists()
    assert pkg.total_tokens() >= 1
