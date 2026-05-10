"""Password hashing helpers for dashboard users."""

from __future__ import annotations

import bcrypt


def hash_password(password: str, *, rounds: int = 12) -> str:
    """Return a bcrypt hash compatible with ui_server verification."""
    if not password:
        raise ValueError("password_required")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds))
    return hashed.decode("utf-8")
