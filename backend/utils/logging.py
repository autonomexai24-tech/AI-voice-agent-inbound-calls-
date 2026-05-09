"""Structured JSON logging (Phase 1).

Per ARCHITECTURE.md §11 and EXECUTION.md §9:
- All production logs are JSON-structured.
- Every log line in call context includes call_id; in tenant context, tenant_id.
- No print() for production logs.

Implementation uses `python-json-logger` (already in requirements.txt).
Consumers obtain a logger with `get_logger(__name__)` and pass context via
the `extra` parameter:

    logger.info("call.started", extra={"call_id": cid, "tenant_id": tid})

Phase 1 scope: scaffolding only. The existing runtime continues to use the
standard library `logging` module directly. This module is available for
new code in backend/ and will be adopted broadly in Phase 2.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from pythonjsonlogger import jsonlogger


# Log fields included on every record when present. python-json-logger will
# attach `extra={...}` fields automatically; this list defines the standard
# set that callers should populate in call / tenant contexts.
_STANDARD_FIELDS = (
    "timestamp",
    "level",
    "name",
    "service",
    "message",
    "call_id",
    "tenant_id",
    "operation",
    "error_type",
)

_configured = False


def configure_logging(
    service: str,
    level: Optional[str] = None,
) -> None:
    """Configure the root logger for JSON output.

    `service` is the logical process name (e.g. "voice-agent", "api").
    `level` defaults to the LOG_LEVEL env var, then INFO.

    Idempotent: safe to call multiple times; only the first call takes effect.
    """
    global _configured
    if _configured:
        return

    resolved_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    # Use a JsonFormatter that renames standard fields to match the schema
    # documented in EXECUTION.md §9.
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        json_ensure_ascii=False,
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Drop any previously configured handlers to avoid duplicate lines.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)

    # Tag every record with the service name via a filter. This way every
    # log line from any module includes "service" without the caller having
    # to pass it in `extra`.
    root.addFilter(_ServiceTagFilter(service))

    _configured = True


class _ServiceTagFilter(logging.Filter):
    """Filter that attaches the service name to every log record."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.service = self._service
        return True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger.

    Does NOT configure logging on its own; processes must call
    `configure_logging(service=...)` at startup. If `configure_logging` has
    not been called, log records fall through to Python's default handler,
    which is acceptable during Phase 1 (new code may be loaded by the old
    runtime, which has not yet adopted structured logging).
    """
    return logging.getLogger(name)


def bind_context(
    logger: logging.Logger,
    *,
    call_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    **extra: object,
) -> logging.LoggerAdapter:
    """Return a LoggerAdapter that injects call/tenant context into every record.

    Use when call-path code emits multiple log lines in the same context.
    Per EXECUTION.md §9, call_id and tenant_id are propagated on every
    line in their respective contexts.

    Example:
        log = bind_context(get_logger(__name__), call_id=cid, tenant_id=tid)
        log.info("tool.check_availability")
        log.info("tool.save_booking_intent")

    Both log lines will include call_id and tenant_id automatically.
    """
    bound: dict[str, object] = {}
    if call_id is not None:
        bound["call_id"] = call_id
    if tenant_id is not None:
        bound["tenant_id"] = tenant_id
    bound.update(extra)
    return _ContextAdapter(logger, bound)


class _ContextAdapter(logging.LoggerAdapter):
    """LoggerAdapter that merges bound context into each record's `extra`."""

    def process(self, msg, kwargs):  # type: ignore[override]
        existing = kwargs.get("extra") or {}
        merged = {**self.extra, **existing}
        kwargs["extra"] = merged
        return msg, kwargs
