"""Runtime logging and request tracing utilities."""

from __future__ import annotations

import os
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.utils.logging import (
    bind_context,
    configure_logging,
    get_logger,
    reset_correlation_context,
    set_correlation_context,
)

try:
    from prometheus_client import Counter, Histogram
except Exception:  # noqa: BLE001
    Counter = None
    Histogram = None


logger = get_logger("backend.logging")

if Counter and Histogram:
    HTTP_REQUESTS_TOTAL = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    HTTP_REQUEST_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "path"],
        buckets=[0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    )
else:
    HTTP_REQUESTS_TOTAL = None
    HTTP_REQUEST_DURATION = None


class RequestTracingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service: str = "api") -> None:
        super().__init__(app)
        self.service = service
        self.log = get_logger("http.request")

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        tokens = set_correlation_context(request_id=request_id)
        started = time.perf_counter()
        status_code = 500
        route_path = request.url.path
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers.setdefault("X-Request-ID", request_id)
            return response
        except Exception as exc:
            self.log.exception(
                "http.request.failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": route_path,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            route = request.scope.get("route")
            metric_path = getattr(route, "path", route_path)
            if HTTP_REQUESTS_TOTAL:
                HTTP_REQUESTS_TOTAL.labels(request.method, metric_path, str(status_code)).inc()
                HTTP_REQUEST_DURATION.labels(request.method, metric_path).observe(duration_ms / 1000)
            self.log.info(
                "http.request.completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": metric_path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            reset_correlation_context(tokens)


def init_sentry(service: str) -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentry.init.skipped", extra={"service": service, "error_type": type(exc).__name__})
        return

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        integrations=[AsyncioIntegration()],
        environment=os.environ.get("ENVIRONMENT", "production"),
        send_default_pii=False,
        before_send=_before_send,
    )
    logger.info("sentry.init.ok", extra={"service": service})


def _before_send(event, hint):  # noqa: ANN001
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    for key in list(headers.keys()):
        if key.lower() in {"authorization", "cookie", "x-admin-token"}:
            headers[key] = "[REDACTED]"
    request["headers"] = headers
    event["request"] = request
    return event


__all__ = [
    "RequestTracingMiddleware",
    "bind_context",
    "configure_logging",
    "get_logger",
    "init_sentry",
    "reset_correlation_context",
    "set_correlation_context",
]
