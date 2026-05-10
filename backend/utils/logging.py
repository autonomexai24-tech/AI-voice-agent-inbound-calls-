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
import re
import sys
from contextvars import ContextVar
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
    "request_id",
    "call_id",
    "tenant_id",
    "did",
    "operation",
    "error_type",
)

_configured = False
_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_tenant_id_ctx: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)
_call_id_ctx: ContextVar[Optional[str]] = ContextVar("call_id", default=None)
_did_ctx: ContextVar[Optional[str]] = ContextVar("did", default=None)
_REDACTION_PATTERNS = (
    re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|token|secret|password)=)[^&\s]+", re.IGNORECASE),
)


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
        fmt=(
            "%(asctime)s %(levelname)s %(name)s %(service)s %(request_id)s "
            "%(tenant_id)s %(call_id)s %(did)s %(message)s"
        ),
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        json_ensure_ascii=False,
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_CorrelationFilter(service))

    root = logging.getLogger()
    # Drop any previously configured handlers to avoid duplicate lines.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)

    _configured = True


class _CorrelationFilter(logging.Filter):
    """Attach service/correlation defaults and redact common secret shapes."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.service = getattr(record, "service", None) or self._service
        record.request_id = getattr(record, "request_id", None) or _request_id_ctx.get() or "-"
        record.tenant_id = getattr(record, "tenant_id", None) or _tenant_id_ctx.get() or "-"
        record.call_id = getattr(record, "call_id", None) or _call_id_ctx.get() or "-"
        record.did = getattr(record, "did", None) or _did_ctx.get() or "-"
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_value(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_value(value) for key, value in record.args.items()}
        for key, value in list(record.__dict__.items()):
            if key in {"msg", "args", "exc_info", "exc_text", "stack_info"}:
                continue
            if _looks_sensitive_key(key):
                setattr(record, key, "[REDACTED]")
            else:
                setattr(record, key, _redact_value(value))
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
    did: Optional[str] = None,
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
    if did is not None:
        bound["did"] = did
    bound.update(extra)
    return _ContextAdapter(logger, bound)


def set_correlation_context(
    *,
    request_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    call_id: Optional[str] = None,
    did: Optional[str] = None,
):
    """Set process-local log correlation values for the current async context."""
    tokens = []
    if request_id is not None:
        tokens.append((_request_id_ctx, _request_id_ctx.set(request_id)))
    if tenant_id is not None:
        tokens.append((_tenant_id_ctx, _tenant_id_ctx.set(tenant_id)))
    if call_id is not None:
        tokens.append((_call_id_ctx, _call_id_ctx.set(call_id)))
    if did is not None:
        tokens.append((_did_ctx, _did_ctx.set(did)))
    return tokens


def reset_correlation_context(tokens) -> None:
    for ctx_var, token in reversed(tokens or []):
        ctx_var.reset(token)


class _ContextAdapter(logging.LoggerAdapter):
    """LoggerAdapter that merges bound context into each record's `extra`."""

    def process(self, msg, kwargs):  # type: ignore[override]
        existing = kwargs.get("extra") or {}
        merged = {**self.extra, **existing}
        kwargs["extra"] = merged
        return msg, kwargs


def _redact_value(value):
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _REDACTION_PATTERNS:
        if pattern.pattern.startswith("(Bearer"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif "api" in pattern.pattern:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ("password", "secret", "api_key", "apikey", "token"))
