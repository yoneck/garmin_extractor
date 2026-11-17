"""Privacy redaction tests."""
from pathlib import Path

from gar.context.privacy import redact_text


def test_redact_email_phone_coords():
    text = "contact me at foo@example.com or +48 123 456 789; coords 48.8566, 2.3522"
    redacted = redact_text(text)
    assert "foo@example.com" not in redacted
    assert "48.8566, 2.3522" not in redacted
    assert "[redacted]" in redacted
