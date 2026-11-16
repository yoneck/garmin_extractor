"""Lightweight terminal progress bar used by the sync pipeline.

No external dependencies. On a TTY the bar redraws in place; when output is
redirected it prints one line per update so logs stay readable.
"""

from __future__ import annotations

import sys
from typing import IO


_BLOCKS = ("\u2588", "\u2591")  # █ ░
_ASCII = ("#", "-")


def _supports_unicode(stream: IO[str]) -> bool:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "█".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


class ProgressBar:
    """Render ``[████░░░░] 3/10 30% | label``.

    Args:
        total: number of steps (must be > 0).
        label: initial label.
        stream: output stream, defaults to stderr.
        width: bar width in characters.
        enabled: set False for tests / quiet mode.
    """

    def __init__(
        self,
        total: int,
        label: str = "",
        stream: IO[str] | None = None,
        width: int = 28,
        enabled: bool = True,
    ) -> None:
        if total <= 0:
            raise ValueError("total must be > 0")
        self.total = int(total)
        self.label = label
        self.stream = stream or sys.stderr
        self.width = max(4, int(width))
        self.enabled = enabled
        self.step = 0
        self._finished = False
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._chars = _BLOCKS if _supports_unicode(self.stream) else _ASCII
        self._last_rendered = ""

    # -- rendering ---------------------------------------------------------
    def _format(self) -> str:
        pct = int(round(100 * self.step / self.total))
        filled = int(round(self.width * self.step / self.total))
        bar = self._chars[0] * filled + self._chars[1] * (self.width - filled)
        text = f"[{bar}] {self.step}/{self.total} {pct:3d}%"
        if self.label:
            text += f" | {self.label}"
        return text

    def _render(self) -> None:
        if not self.enabled:
            return
        text = self._format()
        if self._tty:
            pad = max(0, len(self._last_rendered) - len(text))
            self.stream.write("\r" + text + (" " * pad))
        else:
            self.stream.write(text + "\n")
        self.stream.flush()
        self._last_rendered = text

    # -- public API --------------------------------------------------------
    def start(self, label: str | None = None) -> "ProgressBar":
        if label is not None:
            self.label = label
        self.step = 0
        self._render()
        return self

    def advance(self, label: str | None = None) -> "ProgressBar":
        return self.set_step(self.step + 1, label)

    def set_step(self, step: int, label: str | None = None) -> "ProgressBar":
        self.step = max(0, min(self.total, int(step)))
        if label is not None:
            self.label = label
        self._render()
        if self.step >= self.total:
            self.finish()
        return self

    def finish(self, label: str | None = None) -> "ProgressBar":
        if label is not None:
            self.label = label
        if self._finished:
            return self
        self.step = self.total
        self._render()
        if self._tty:
            self.stream.write("\n")
            self.stream.flush()
        self._finished = True
        return self


def stage_bar(total: int, label: str = "sync", enabled: bool = True) -> ProgressBar:
    """Convenience factory used by the CLI."""
    return ProgressBar(total=total, label=label, enabled=enabled)
