"""Environment variable validation (fail-fast).

Per RULES.md §4 and EXECUTION.md §6:
- Application startup must fail fast if critical env vars are missing.
- Never silently default, never start in a degraded state.
- Never mutate os.environ at runtime.

This module is a validator only. It does NOT load .env files (python-dotenv
or equivalent should do that before calling validate_env). It does NOT
mutate os.environ.

Phase 1 scope: defines the validation API and the Postgres-specific set.
Existing runtime (agent.py, ui_server.py) is not yet wired to call this.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional


# Core env vars required by the voice pipeline. These are documented in
# ARCHITECTURE.md §8 and .env.example. Validated when voice agent starts.
REQUIRED_CORE_ENV: tuple[str, ...] = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "OPENAI_API_KEY",
    "SARVAM_API_KEY",
)

# Additional env vars required only when USE_POSTGRES=true. The legacy
# Supabase env vars (SUPABASE_URL, SUPABASE_KEY) are NOT listed here; they
# remain required by the existing db.py until Phase 2 migration completes.
REQUIRED_POSTGRES_ENV: tuple[str, ...] = (
    "DATABASE_URL",
)


class EnvValidationError(RuntimeError):
    """Raised when required env vars are missing. Triggers fail-fast exit."""


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return an env var value, or a default if unset.

    Leading/trailing whitespace is stripped. Empty strings are treated as
    unset (and the default is returned instead).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    stripped = raw.strip()
    if not stripped:
        return default
    return stripped


def require_env(name: str) -> str:
    """Return a required env var or raise EnvValidationError."""
    value = get_env(name)
    if value is None:
        raise EnvValidationError(
            f"Required environment variable '{name}' is missing or empty."
        )
    return value


def validate_env(names: Iterable[str]) -> None:
    """Validate that every name in `names` is set to a non-empty value.

    Raises EnvValidationError listing every missing name. Callers should
    let this exception propagate; the process should exit non-zero so the
    container orchestrator (Supervisor / EasyPanel) can surface the failure.
    """
    missing = [n for n in names if get_env(n) is None]
    if missing:
        raise EnvValidationError(
            "Missing required environment variables: " + ", ".join(missing)
        )
