"""MFA prompt wrapper for the ``garminconnect`` library.

The extractor asks the user for a verification code when Garmin requires
two-factor confirmation. Passing ``EOFError`` for non-interactive runners helps
them fail cleanly with a clear message instead of a confusing stack trace.
"""

from __future__ import annotations


class MfaCode:
    def run(self) -> str:
        try:
            code = input("Please enter the MFA code (6 digits): ").strip()
        except EOFError:
            raise SystemExit(
                "\nInteractive MFA is required but no terminal is available. "
                "Provide valid credentials to continue."
            )
        if len(code) < 4:
            raise ValueError("MFA code must be at least 4 characters.")
        return code


def get_mfa_prompt(config: dict) -> MfaCode:
    return MfaCode()
