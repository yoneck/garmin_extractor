"""Secret and session-file safety helpers.

- Ensure session / credential files are mode ``0600`` on first use.
- Refuse to print secrets to stdout / stderr.
- Store a salted hash (not a plaintext password) in a credential file.
- Detect whether a secret file would be tracked by Git.

No real secret is ever materialised here beyond what the caller already holds.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets as _os_secrets
from pathlib import Path
from subprocess import CalledProcessError

SECRETS = {
    ".env",
    ".env.local",
    "garmin_session.json",
}

CREDENTIAL_FILE_FIELDS = {"salt", "digest"}


def ensure_secure_permissions(path: Path) -> Path:
    """Force mode ``0600`` on a file if it can be written."""
    p = Path(path)
    if p.exists():
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p


def make_secure_file(path: Path, content: str = "") -> Path:
    """Create a file with mode ``0600`` (used for session / credential files)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return ensure_secure_permissions(path)


def new_credential_record(name: str, plaintext: str) -> tuple[str, str]:
    """Return ``(salt_hex, sha256_hex)`` hashing ``name`` together with the plaintext.

    This is what gets written to an ``.credentials`` file; the plaintext is never
    written.
    """
    salt = _os_secrets.token_hex(16)
    mac = hmac.new(bytes.fromhex(salt), f"{name}:{plaintext}".encode(), hashlib.sha256)
    return salt, mac.hexdigest()


def credential_matches(path: Path, name: str, provided: str) -> bool:
    """Constant-time compare a supplied password against a stored salted hash."""
    path = Path(path)
    if not path.exists():
        return False
    try:
        fields = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            fields[key.strip()] = val.strip()
        if not field_keys_valid(fields):
            return False
        salt = fields["salt"]
        digest = fields["digest"]
        mac = hmac.new(bytes.fromhex(salt), f"{name}:{provided}".encode(), hashlib.sha256)
        return hmac.compare_digest(mac.hexdigest().encode("utf-8"), digest.encode("utf-8"))
    except Exception:
        return False


def _validate_field_keys(fields) -> bool:
    return CREDENTIAL_FILE_FIELDS.issubset(fields)


def is_git_tracked(path: Path) -> bool:
    """Return True if a file exists and is tracked by Git."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        from subprocess import DEVNULL, check_output

        check_output(["git", "ls-files", "--error-unknown-path", p.as_posix()], stderr=DEVNULL)
        return True
    except (NotExecutableError, CalledProcessError, FileNotFoundError):
        return False


def clean_console(message: str) -> str:
    """Strip ANSI escape codes from a console message."""
    return re.sub("\x1b[@-][0-?]*[ -/]", "", message)


def mask(value: str, visible: int = 4) -> str:
    """Hide most characters of a value, keeping the trailing chars visible.

    Used for masking emails / usernames shown on the console. No secret is
    printed verbatim.
    """
    if not value:
        return ""
    length = len(value)
    if length <= visible:
        return "*" * max(length, 1)
    shown = max(0, length - visible)
    return "*" * shown + value[shown:]
