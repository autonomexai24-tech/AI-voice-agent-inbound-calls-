"""Pure validation helpers (Phase 2 scaffolding).

All functions here are deterministic, side-effect-free, and cheap enough
to call in request paths. None of them touch the voice pipeline.

No regex compilation at call time — patterns compiled once at module load.
"""

from __future__ import annotations

import re
from datetime import datetime


_E164_PHONE_RE = re.compile(r"^\+?\d{7,15}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_e164_phone(value: str) -> bool:
    """Loose E.164 check: optional leading '+', then 7–15 digits.

    Matches the pattern used by the existing agent.py caller extraction,
    so values that flow through the voice pipeline stay consistent.
    """
    if not isinstance(value, str):
        return False
    return bool(_E164_PHONE_RE.match(value.strip()))


def is_indian_phone(value: str) -> bool:
    """Stricter check: must start with +91 and be 13 chars total."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped.startswith("+91") and len(stripped) == 13 and stripped[1:].isdigit()


def is_valid_email(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_EMAIL_RE.match(value.strip()))


def is_iso8601_datetime(value: str) -> bool:
    """True if `value` is a datetime parseable by datetime.fromisoformat."""
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False
