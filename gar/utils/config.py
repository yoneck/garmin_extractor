"""Configuration loading with ``load_dotenv`` and a safe fallback parser.

This module never stores secrets. It only reads them from ``os.environ`` (highest
precedence) then from a ``.env`` file next to the project, falling back to a tiny
``.env`` parser when ``python-dotenv`` is unavailable. Credentials read here are
returned to the caller and are never written back to the loaded environment unless
the caller explicitly asks for it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class ConfigError(Exception):
    """Raised when required configuration is missing."""


def parse_env_file(env_path: Path) -> dict[str, str]:
    """Parse a ``.env`` file safely.

    Returns a plain dict of key/value pairs. Keys are validated against an allow
    list of recognised config variables so that arbitrary values cannot be pushed
    into the process environment.
    """
    recognised = {
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
        "GARMIN_SESSION_FILE",
        "GARMIN_CREDENTIAL_FILE",
        "GARMIN_DATA_DIR",
        "GARMIN_LOG_DIR",
        "GARMIN_MFA_PROMPT",
        "GARMIN_BODY_COMP_DATASET",
    }
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key in recognised and key not in values:
            values[key] = value
    return values


def load_from_env(env_path: Path, strict: bool = False) -> dict[str, str]:
    """Load recognised config values.

    Precedence: already set environment variables win; otherwise values from
    ``.env`` fill in the gaps. If ``strict`` is set and any required key is
    missing, raise :class:`ConfigError`.
    """
    loaded: dict[str, str] = {}
    use_dotenv = True
    if use_dotenv:
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=env_path, override=False)
        except Exception:
            use_dotenv = False

    if not use_dotenv:
        loaded.update(parse_env_file(env_path))

    effective: dict[str, str] = {}
    for key in (
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
        "GARMIN_SESSION_FILE",
        "GARMIN_CREDENTIAL_FILE",
        "GARMIN_DATA_DIR",
        "GARMIN_LOG_DIR",
        "GARMIN_MFA_PROMPT",
        "GARMIN_BODY_COMP_DATASET",
    ):
        value = os.environ.get(key) or loaded.get(key)
        if value:
            effective[key] = value

    required = ("GARMIN_EMAIL", "GARMIN_PASSWORD")
    if strict:
        missing = [k for k in required if k not in effective]
        if missing:
            raise ConfigError(
                "Missing required configuration: " + ", ".join(missing)
            )
    return effective


def resolve_path(config: dict[str, str], key: str, default: str) -> Path:
    """Resolve a path config key against the project directory if relative."""
    value = config.get(key) or default
    return Path(value)


def as_optional(config: dict[str, str], key: str) -> Optional[str]:
    return config.get(key) or None
