"""Persistent sync watermarks for incremental extraction."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SyncState:
    """JSON-backed per-dataset watermark store.

    The file is written atomically and guarded by a lock so interrupted runs
    never advance a watermark.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {"datasets": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"datasets": {}}
        if not isinstance(data, dict):
            return {"datasets": {}}
        data.setdefault("datasets", {})
        return data

    def get(self, dataset: str) -> dict:
        return dict(self.load().get("datasets", {}).get(dataset, {}))

    def watermark(self, dataset: str) -> datetime | None:
        return _parse_dt(self.get(dataset).get("watermark_utc"))

    def update(
        self,
        dataset: str,
        watermark: datetime | None = None,
        status: str = "ok",
        rows: int = 0,
        error: str | None = None,
    ) -> None:
        state = self.load()
        datasets = state.setdefault("datasets", {})
        entry = dict(datasets.get(dataset, {}))
        entry["status"] = status
        entry["rows"] = int(rows)
        entry["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        if watermark is not None:
            entry["watermark_utc"] = watermark.astimezone(timezone.utc).isoformat()
        if error:
            entry["error"] = str(error)
        else:
            entry.pop("error", None)
        datasets[dataset] = entry
        self._write(state)

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        lock = FileLock(str(self.path) + ".lock")
        with lock:
            tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.path)
