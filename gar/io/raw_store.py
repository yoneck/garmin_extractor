"""Lossless raw archive for Garmin API responses."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filelock import FileLock

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawStore:
    """Write/read gzipped JSON responses with a manifest.

    Layout::

        garmin_data/raw/{dataset}/{YYYY}/{MM}/{key}.json.gz
        garmin_data/raw/manifest.jsonl
    """

    def __init__(self, base_dir: Path, enabled: bool = True):
        self.base_dir = Path(base_dir)
        self.enabled = bool(enabled)

    def _safe_key(self, key: str) -> str:
        key = _SAFE.sub("_", str(key)).strip("_")
        if len(key) > 72:
            digest = hashlib.sha256(str(key).encode()).hexdigest()[:12]
            key = key[:60] + "-" + digest
        return key or "response"

    def path(self, dataset: str, key: str, when: datetime | None = None) -> Path:
        when = when or _utcnow()
        return (
            self.base_dir
            / "raw"
            / dataset
            / f"{when:%Y}"
            / f"{when:%m}"
            / f"{self._safe_key(key)}.json.gz"
        )

    def has(self, dataset: str, key: str) -> bool:
        # Keys may have been written in a previous month; check both current
        # and previous month to make resume across a month boundary work.
        now = _utcnow()
        candidates = [self.path(dataset, key, now)]
        previous = now.replace(day=1) - timedelta(days=1)
        candidates.append(self.path(dataset, key, previous))
        return any(path.exists() for path in candidates)

    def get(self, dataset: str, key: str):
        now = _utcnow()
        candidates = [self.path(dataset, key, now)]
        previous = now.replace(day=1) - timedelta(days=1)
        candidates.append(self.path(dataset, key, previous))
        for path in candidates:
            if path.exists():
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    return json.load(handle)
        return None

    def put(
        self,
        dataset: str,
        endpoint: str,
        params: dict | None,
        payload,
        key: str,
    ) -> dict | None:
        if not self.enabled:
            return None
        when = _utcnow()
        path = self.path(dataset, key, when)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as handle:
            handle.write(raw.decode("utf-8"))
        os.replace(tmp, path)

        manifest = {
            "dataset": dataset,
            "endpoint": endpoint,
            "params": params or {},
            "key": key,
            "fetched_at_utc": when.isoformat(),
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        manifest_path = self.base_dir / "raw" / "manifest.jsonl"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(manifest_path) + ".lock"):
            with manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, default=str) + "\n")
        return manifest
