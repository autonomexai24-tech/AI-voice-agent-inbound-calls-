"""Centralized, env-driven application configuration (Phase 2).

Design:
- Pure read-only. Never mutates os.environ (EXECUTION.md §6).
- Resolved once at startup via `load_app_config()` and treated as immutable.
- Typed accessors via the `AppConfig` dataclass.
- No secrets in defaults. No config.json. No inline fallbacks.
- Fail-fast validation for required env vars per subsystem.

Phase 2 scope: scaffolding. The existing runtime (agent.py, ui_server.py)
continues to read from os.environ directly. Phase 3 will wire call sites to
this module when modularizing the voice agent.

Subsystem groupings mirror docs/ARCHITECTURE.md §8 environment variables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend.config.env import (
    REQUIRED_CORE_ENV,
    REQUIRED_POSTGRES_ENV,
    get_env,
    validate_env,
)


@dataclass(frozen=True)
class LiveKitConfig:
    url: str
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str


@dataclass(frozen=True)
class SarvamConfig:
    api_key: str


@dataclass(frozen=True)
class CalcomConfig:
    api_key: Optional[str]
    event_type_id: Optional[str]


@dataclass(frozen=True)
class VobizConfig:
    sip_domain: Optional[str]
    default_transfer_number: Optional[str]


@dataclass(frozen=True)
class PostgresConfig:
    enabled: bool
    database_url: Optional[str]
    pool_min: int
    pool_max: int


@dataclass(frozen=True)
class ObservabilityConfig:
    sentry_dsn: Optional[str]
    environment: str
    log_level: str


@dataclass(frozen=True)
class AppConfig:
    """Resolved application configuration. Immutable."""

    livekit: LiveKitConfig
    openai: OpenAIConfig
    sarvam: SarvamConfig
    calcom: CalcomConfig
    vobiz: VobizConfig
    postgres: PostgresConfig
    observability: ObservabilityConfig
    raw_required_missing: tuple[str, ...] = field(default_factory=tuple)


def _postgres_enabled() -> bool:
    return (get_env("USE_POSTGRES") or "false").strip().lower() == "true"


def load_app_config(strict: bool = True) -> AppConfig:
    """Load and validate application configuration from environment.

    When `strict=True` (the default for the voice agent / API processes),
    required env vars are validated and missing ones raise via
    `validate_env`. When `strict=False`, the function returns an
    AppConfig with whatever is present and records the missing core vars
    in `raw_required_missing` so a caller can decide what to do.

    This function does NOT call `load_dotenv()`. The process entrypoint
    should load `.env` before calling this (existing agent.py already does).
    """
    if strict:
        validate_env(REQUIRED_CORE_ENV)

    missing: list[str] = []
    for name in REQUIRED_CORE_ENV:
        if get_env(name) is None:
            missing.append(name)

    return AppConfig(
        livekit=LiveKitConfig(
            url=get_env("LIVEKIT_URL") or "",
            api_key=get_env("LIVEKIT_API_KEY") or "",
            api_secret=get_env("LIVEKIT_API_SECRET") or "",
        ),
        openai=OpenAIConfig(
            api_key=get_env("OPENAI_API_KEY") or "",
        ),
        sarvam=SarvamConfig(
            api_key=get_env("SARVAM_API_KEY") or "",
        ),
        calcom=CalcomConfig(
            api_key=get_env("CAL_API_KEY"),
            event_type_id=get_env("CAL_EVENT_TYPE_ID"),
        ),
        vobiz=VobizConfig(
            sip_domain=get_env("VOBIZ_SIP_DOMAIN"),
            default_transfer_number=get_env("DEFAULT_TRANSFER_NUMBER"),
        ),
        postgres=PostgresConfig(
            enabled=_postgres_enabled(),
            database_url=get_env("DATABASE_URL"),
            pool_min=int(get_env("POSTGRES_POOL_MIN") or "1"),
            pool_max=int(get_env("POSTGRES_POOL_MAX") or "10"),
        ),
        observability=ObservabilityConfig(
            sentry_dsn=get_env("SENTRY_DSN"),
            environment=get_env("ENVIRONMENT") or "production",
            log_level=(get_env("LOG_LEVEL") or "INFO").upper(),
        ),
        raw_required_missing=tuple(missing),
    )
