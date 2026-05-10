"""PostgreSQL connection pool management.

Design (per ARCHITECTURE.md and EXECUTION.md):
- psycopg2.pool.ThreadedConnectionPool (thread-safe, shared across API + agent processes within container)
- DATABASE_URL is the single source of truth for connection info
- USE_POSTGRES feature flag gates pool initialization
- Fail-fast: if USE_POSTGRES=true but DATABASE_URL is missing, initialization raises
- No silent fallbacks, no default DSNs, no hardcoded credentials

Usage:
    from backend.db.connection import init_pool, get_connection

    # At process startup (once):
    init_pool()

    # Per query:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extensions import connection as PGConnection

from backend.utils.logging import get_logger

logger = get_logger("backend.db.connection")

# Default pool sizing. Kept small on purpose: a single container runs voice
# agent + API + frontend; per-process demand is low. Can be tuned via env.
_DEFAULT_MIN_CONN = 1
_DEFAULT_MAX_CONN = 10

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def is_postgres_enabled() -> bool:
    """Return True if the USE_POSTGRES feature flag is enabled."""
    return os.environ.get("USE_POSTGRES", "").strip().lower() == "true"


def _validate_env_for_postgres() -> str:
    """Validate that required env vars are present when PG is enabled.

    Returns the validated DATABASE_URL. Raises RuntimeError on failure.
    Fail-fast policy: never return a default, never silently disable.
    """
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "USE_POSTGRES=true but DATABASE_URL is not set. "
            "Refusing to start in a degraded state. "
            "Set DATABASE_URL to a valid PostgreSQL connection string."
        )
    return database_url


def init_pool(
    min_conn: Optional[int] = None,
    max_conn: Optional[int] = None,
) -> Optional[pg_pool.ThreadedConnectionPool]:
    """Initialize the global PostgreSQL connection pool.

    If USE_POSTGRES is not enabled, this is a no-op and returns None. This
    allows local non-PostgreSQL test processes to start unchanged.

    If USE_POSTGRES is enabled, this validates env vars and opens the pool.
    Raises RuntimeError on misconfiguration (fail-fast).

    Idempotent: safe to call multiple times; subsequent calls return the
    existing pool.
    """
    global _pool

    if not is_postgres_enabled():
        logger.info(
            "postgres.pool.skip",
            extra={"reason": "USE_POSTGRES is not 'true'"},
        )
        return None

    with _pool_lock:
        if _pool is not None:
            return _pool

        database_url = _validate_env_for_postgres()

        resolved_min = (
            min_conn
            if min_conn is not None
            else int(os.environ.get("POSTGRES_POOL_MIN", _DEFAULT_MIN_CONN))
        )
        resolved_max = (
            max_conn
            if max_conn is not None
            else int(os.environ.get("POSTGRES_POOL_MAX", _DEFAULT_MAX_CONN))
        )

        try:
            _pool = pg_pool.ThreadedConnectionPool(
                minconn=resolved_min,
                maxconn=resolved_max,
                dsn=database_url,
            )
        except psycopg2.Error as e:
            logger.error(
                "postgres.pool.init_failed",
                extra={"error_type": type(e).__name__},
            )
            raise RuntimeError("Failed to initialize PostgreSQL pool") from e

        logger.info(
            "postgres.pool.initialized",
            extra={"min_conn": resolved_min, "max_conn": resolved_max},
        )
        return _pool


def close_pool() -> None:
    """Close all connections and tear down the pool. Safe to call at shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
                logger.info("postgres.pool.closed")
            except Exception as e:  # noqa: BLE001
                logger.error("postgres.pool.close_error", extra={"error_type": type(e).__name__})
            _pool = None


@contextmanager
def get_connection() -> Iterator[PGConnection]:
    """Borrow a connection from the pool as a context manager.

    Commits on successful exit, rolls back on exception, and always returns
    the connection to the pool. Raises RuntimeError if the pool is not
    initialized (i.e. USE_POSTGRES is disabled).
    """
    if _pool is None:
        raise RuntimeError(
            "PostgreSQL pool is not initialized. "
            "Call init_pool() at process startup and ensure USE_POSTGRES=true."
        )

    conn: PGConnection = _pool.getconn()
    started = time.perf_counter()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if duration_ms >= float(os.environ.get("POSTGRES_SLOW_OPERATION_MS", "500")):
            logger.warning("postgres.operation.slow", extra={"duration_ms": duration_ms})
        _pool.putconn(conn)


def healthcheck() -> dict:
    """Return pool health status for /health endpoint integration.

    Does not raise. Returns a structured dict safe to serialize as JSON.
    """
    if not is_postgres_enabled():
        return {"postgres": "disabled"}
    if _pool is None:
        return {"postgres": "uninitialized"}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"postgres": "ok", **_pool_stats()}
    except Exception as e:  # noqa: BLE001
        return {"postgres": "error", "detail": type(e).__name__, **_pool_stats()}


def _pool_stats() -> dict:
    if _pool is None:
        return {}
    return {
        "minconn": getattr(_pool, "minconn", None),
        "maxconn": getattr(_pool, "maxconn", None),
        "used": len(getattr(_pool, "_used", {}) or {}),
        "available": len(getattr(_pool, "_pool", []) or []),
    }
