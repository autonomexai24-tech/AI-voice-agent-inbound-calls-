"""Startup validation hooks (fail-fast, no side effects on import).

Per RULES.md §4 and EXECUTION.md §6, each process must validate its
required env vars at startup and either run or exit with a clear error.

Phase 2 scope: scaffolding. This module is importable but is not yet
wired into agent.py, ui_server.py, or future FastAPI factories. Phase 3
will call `run_startup_checks()` from process entrypoints.

Usage:
    from backend.core.startup import run_startup_checks

    if __name__ == "__main__":
        run_startup_checks("voice-agent")
        # ... existing entrypoint code ...
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from backend.config.env import EnvValidationError
from backend.core.config import load_app_config
from backend.db.connection import init_pool, is_postgres_enabled
from backend.utils.logging import configure_logging, get_logger

_STARTUP_STATUS: dict[str, object] = {
    "status": "not_run",
    "service": None,
    "postgres_enabled": False,
    "postgres_initialized": False,
    "last_error": None,
    "checked_at": None,
}


def run_startup_checks(
    service_name: str | None = None,
    initialize_postgres: bool = True,
    strict_config: bool = True,
    **legacy_kwargs: object,
) -> None:
    """Execute the standard startup validation sequence.

    Steps:
      1. Configure JSON logging (idempotent).
      2. Validate env vars via `load_app_config(strict=strict_config)`.
         On failure, this raises EnvValidationError which propagates.
      3. If Postgres feature flag is enabled and `initialize_postgres` is
         True, initialize the connection pool (also fail-fast if misconfigured).

    On any failure, the caller should let the exception propagate so
    Supervisor / EasyPanel restart policies can surface the problem.
    Callers may optionally invoke `_exit_with_error()` themselves.
    """
    if service_name is None:
        service_name = str(legacy_kwargs.get("service") or "unknown-service")

    configure_logging(service=service_name)
    log = get_logger("backend.core.startup")
    _record_startup_status(
        status="running",
        service=service_name,
        postgres_enabled=is_postgres_enabled(),
        postgres_initialized=False,
        last_error=None,
    )

    log.info("startup.begin", extra={"service": service_name})

    # Config validation — only critical voice env vars fail startup.
    try:
        config = load_app_config(strict=strict_config)
    except EnvValidationError:
        log.error("startup.critical_env_missing", extra={"service": service_name})
        _record_startup_status(
            status="failed",
            service=service_name,
            postgres_enabled=is_postgres_enabled(),
            postgres_initialized=False,
            last_error="critical_env_missing",
        )
        raise

    log.info(
        "startup.config_loaded",
        extra={
            "service": service_name,
            "postgres_enabled": config.postgres.enabled,
            "environment": config.observability.environment,
        },
    )

    # Pool init when enabled. Postgres is a coexistence path in Phase 3B:
    # failures degrade to config.json/Supabase fallback and must not crash
    # live calls.
    if initialize_postgres and is_postgres_enabled():
        try:
            init_pool()
            _record_startup_status(
                status="ok",
                service=service_name,
                postgres_enabled=True,
                postgres_initialized=True,
                last_error=None,
            )
            log.info("startup.postgres_initialized", extra={"service": service_name})
        except Exception as exc:  # noqa: BLE001
            _record_startup_status(
                status="degraded",
                service=service_name,
                postgres_enabled=True,
                postgres_initialized=False,
                last_error="postgres_unavailable",
            )
            log.error(
                "startup.postgres_unavailable",
                extra={
                    "service": service_name,
                    "postgres_enabled": True,
                    "error_type": type(exc).__name__,
                },
            )
    else:
        _record_startup_status(
            status="ok",
            service=service_name,
            postgres_enabled=False,
            postgres_initialized=False,
            last_error=None,
        )
        log.info(
            "startup.postgres_disabled",
            extra={"service": service_name, "postgres_enabled": False},
        )

    final_status = _STARTUP_STATUS.get("status")
    log.info(
        "startup.ok" if final_status == "ok" else "startup.degraded",
        extra={"service": service_name, "status": final_status},
    )


def get_startup_status() -> dict[str, object]:
    """Return the last startup validation result for lightweight health."""
    return dict(_STARTUP_STATUS)


def _record_startup_status(
    *,
    status: str,
    service: str,
    postgres_enabled: bool,
    postgres_initialized: bool,
    last_error: str | None,
) -> None:
    _STARTUP_STATUS.update(
        {
            "status": status,
            "service": service,
            "postgres_enabled": postgres_enabled,
            "postgres_initialized": postgres_initialized,
            "last_error": last_error,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _exit_with_error(message: str, code: int = 1) -> None:
    """Convenience helper for entrypoints that want to exit nonzero.

    Writes to stderr (not a logger) to guarantee visibility even if
    logging is misconfigured. Never call this from library code.
    """
    sys.stderr.write(f"[FATAL] {message}\n")
    sys.stderr.flush()
    sys.exit(code)
