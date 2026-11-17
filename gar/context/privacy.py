"""Privacy helpers: redact PII and GPS from context output."""

from __future__ import annotations

import re
from pathlib import Path

# Patterns for common PII.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
# Garmin device serials are long numeric strings.
_SERIAL = re.compile(r"\b\d{8,}\b")
# Latitude/longitude pairs like "48.8566, 2.3522".
_COORD = re.compile(r"-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}")

REDACTION_TOKEN = "[redacted]"


def redact_text(text: str) -> str:
    text = _EMAIL.sub(REDACTION_TOKEN, text)
    text = _PHONE.sub(REDACTION_TOKEN, text)
    text = _COORD.sub(REDACTION_TOKEN, text)
    return text


def redact_path(path: Path) -> Path:
    """Return a sibling path with a ``.redacted`` suffix (no-op helper)."""
    return path.with_suffix(path.suffix + ".redacted")


def redact_context_dir(context_dir: Path) -> None:
    for path in Path(context_dir).glob("*.md"):
        original = path.read_text(encoding="utf-8")
        redacted = redact_text(original)
        if redacted != original:
            path.write_text(redacted, encoding="utf-8")