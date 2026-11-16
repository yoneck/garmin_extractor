"""Tests for the dependency-free progress bar."""
import io

from gar.utils.progress import ProgressBar


def test_renders_percentage_and_counts():
    stream = io.StringIO()
    bar = ProgressBar(total=4, label="sync", stream=stream, enabled=True)
    bar.start()
    bar.advance("activities")
    bar.advance("sleep")
    bar.finish()
    out = stream.getvalue()
    assert "1/4" in out
    assert "2/4" in out
    assert "4/4" in out
    assert "100%" in out
    assert "activities" in out


def test_disabled_writes_nothing():
    stream = io.StringIO()
    bar = ProgressBar(total=3, stream=stream, enabled=False)
    bar.start()
    bar.advance()
    bar.finish()
    assert stream.getvalue() == ""


def test_cannot_exceed_total():
    stream = io.StringIO()
    bar = ProgressBar(total=2, stream=stream, enabled=True)
    bar.set_step(99)
    assert bar.step == 2
