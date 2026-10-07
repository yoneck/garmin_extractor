"""Authentication for the Garmin Connect API.

Reuses the established login mechanism from the previous version:
1. Build a ``garminconnect.Garmin`` client with an MFA prompt callable.
2. Call ``gc.login``.
   protected file (mode 0600) and reused on later runs.

No credentials are ever printed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from garminconnect import Garmin

from ..utils.secrets import mask


class ExtractionError(Exception):
    """Raised when the client cannot be created or the user is not logged in."""


class GarminClient(Garmin):
    """Thin subclass adding an English MFA prompt callable."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prompt_mfa = self.run_mfa

    def run_mfa(self) -> str:
        try:
            code = input("Please enter the MFA code (6 digits): ").strip()
        except EOFError:
            raise SystemExit(
                "\nInteractive MFA is required but no terminal is available. "
                "Provide valid credentials to reconnect."
            )
        if len(code) < 4:
            raise ValueError("MFA code must be at least 4 characters long.")
        return code


def secure_permissions(session_file: str) -> None:
    if not isinstance(session_file, str) or not session_file:
        return
    path = Path(session_file)
    if path.exists() and (path.stat().st_mode & stat.S_IMODE(path.stat().st_mode)) != stat.S_IWUSR:
        path.chmod(0o600)


def verify_login(config: dict) -> str:
    session_file = config.get("GARMIN_SESSION_FILE")
    if not session_file:
        raise ExtractionError("Run setup_config: session file is not configured yet.")
    if not (isinstance(session_file, str) and os.path.exists(session_file)):
        raise ExtractionError("No saved session found. Run setup_config for the first time.")
    secure_permissions(session_file)
    email = config.get("GARMIN_EMAIL")
    return f"Logged in as {mask(email)} using saved session."


def connect(config: dict) -> Garmin:
    """Create the client with login(tokenstore=...) and return it."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    email = config.get("GARMIN_EMAIL")
    password = config.get("GARMIN_PASSWORD")
    if not email or not password:
        raise ExtractionError("Run setup_config: credentials are not configured yet.")

    session_file = config.get("GARMIN_SESSION_FILE") or "garmin_session.json"
    secure_permissions(session_file)

    client = GarminClient(email, password)
    state = client.login(tokenstore=session_file)
    _log_login_state(state)
    return client


def _log_login_state(state) -> None:
    if isinstance(state, (list, tuple)):
        for message in state:
            if message:
                print(message)
        return
    print(state)
