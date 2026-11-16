"""Fetch wrapper: raw archive, retry/backoff and a simple rate limiter."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from gar.io.raw_store import RawStore


class Fetcher:
    """Call Garmin client methods with caching and retries.

    Args:
        client: garminconnect client.
        raw_dir: root for the raw archive (usually ``garmin_data``).
        no_refetch: return archived responses instead of calling the API.
        min_interval: minimum seconds between live requests.
        retries: number of attempts for transient failures.
        raw_enabled: disable the archive for debugging only.
    """

    def __init__(
        self,
        client,
        raw_dir: Path,
        no_refetch: bool = True,
        min_interval: float = 0.5,
        retries: int = 3,
        raw_enabled: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.raw = RawStore(Path(raw_dir), enabled=raw_enabled)
        self.no_refetch = no_refetch
        self.min_interval = max(0.0, float(min_interval))
        self.retries = max(1, int(retries))
        self._sleep = sleep
        self._last_call = 0.0
        self.requests = 0
        self.cache_hits = 0

    def call(
        self,
        dataset: str,
        method: str,
        *args,
        key: str | None = None,
        **kwargs,
    ) -> Any:
        cache_key = key or self._key(method, args, kwargs)
        if self.no_refetch and self.raw.has(dataset, cache_key):
            payload = self.raw.get(dataset, cache_key)
            if payload is not None:
                self.cache_hits += 1
                return payload

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                self._limit()
                payload = getattr(self.client, method)(*args, **kwargs)
                self.requests += 1
                self.raw.put(
                    dataset,
                    method,
                    {"args": list(args), "kwargs": kwargs},
                    payload,
                    cache_key,
                )
                return payload
            except Exception as error:  # noqa: BLE001
                last_error = error
                if attempt >= self.retries:
                    break
                self._sleep(min(8.0, 2 ** (attempt - 1)))
        raise RuntimeError(f"{dataset}.{method} failed after {self.retries} attempts: {last_error}")

    def _limit(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        wait = self.min_interval - (now - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    @staticmethod
    def _key(method: str, args, kwargs) -> str:
        parts = [method]
        parts.extend(str(arg) for arg in args)
        parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return "-".join(parts)
