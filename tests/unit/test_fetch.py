"""Fetch layer tests (no network)."""
from pathlib import Path

from gar.io.fetch import Fetcher


class Client:
    def __init__(self):
        self.calls = 0

    def ping(self, value):
        self.calls += 1
        return {"value": value}


def test_raw_archive_is_reused(tmp_path: Path):
    client = Client()
    fetcher = Fetcher(client, tmp_path, min_interval=0, sleep=lambda _: None)
    assert fetcher.call("demo", "ping", 1, key="2026-11-15") == {"value": 1}
    assert fetcher.call("demo", "ping", 1, key="2026-11-15") == {"value": 1}
    assert client.calls == 1
    assert fetcher.requests == 1
    assert fetcher.cache_hits == 1
    assert list((tmp_path / "raw" / "demo").rglob("*.json.gz"))
    assert (tmp_path / "raw" / "manifest.jsonl").exists()


def test_no_refetch_false_forces_call(tmp_path: Path):
    client = Client()
    fetcher = Fetcher(
        client, tmp_path, no_refetch=False, min_interval=0, sleep=lambda _: None
    )
    fetcher.call("demo", "ping", 1, key="k")
    fetcher.call("demo", "ping", 1, key="k")
    assert client.calls == 2


def test_retry_then_success(tmp_path: Path):
    class Flaky(Client):
        def ping(self, value):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("transient")
            return {"value": value}

    client = Flaky()
    fetcher = Fetcher(
        client, tmp_path, min_interval=0, retries=3, sleep=lambda _: None
    )
    assert fetcher.call("demo", "ping", 7) == {"value": 7}
    assert client.calls == 3
