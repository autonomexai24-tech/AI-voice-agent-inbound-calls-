"""Lightweight health aggregation for the /health endpoint.

Phase 2 scope: scaffolding. This aggregator composes checks from subsystems
already available (PostgreSQL connection) and returns a structured status
dict safe to serialize as JSON.

Design constraints (per RULES.md and EXECUTION.md):
- Must never raise to the caller.
- Must not run heavy operations (no test inserts, no end-to-end call).
- Must complete in well under a second so EasyPanel probes don't time out.
- Must not log secrets, API keys, or DSNs.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from backend.config.env import REQUIRED_CORE_ENV, get_env
from backend.core.config_resolver import get_config_source_status
from backend.core.startup import get_startup_status
from backend.db.connection import healthcheck as postgres_healthcheck

CONFIG_FILE = "config.json"


def aggregate_health(service: str = "ai-receptionist") -> dict[str, Any]:
    """Return the full health payload for the /health endpoint.

    Shape:
        {
            "status": "ok" | "degraded",
            "service": "<name>",
            "timestamp": "<ISO8601 UTC>",
            "checks": {
                "postgres": {"postgres": "ok" | "disabled" | ...},
            },
        }

    Status is "ok" if every subsystem is either healthy or explicitly
    disabled; "degraded" if any subsystem returns an error.
    """
    checks: dict[str, Any] = {
        "process": _process_status(),
        "postgres": postgres_healthcheck(),
        "config_source": get_config_source_status(),
        "startup_validation": _startup_validation_status(),
    }

    degraded = any(
        isinstance(v, dict) and v.get(k) == "error"
        for k, v in checks.items()
    )
    if checks["startup_validation"]["status"] == "missing_critical_env":
        degraded = True

    startup_status = get_startup_status()
    if startup_status.get("status") in {"failed", "degraded"}:
        degraded = True
    checks["startup_state"] = startup_status

    return {
        "status": "degraded" if degraded else "ok",
        "service": service,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


def _startup_validation_status() -> dict[str, Any]:
    missing = [name for name in REQUIRED_CORE_ENV if get_env(name) is None]
    return {
        "status": "missing_critical_env" if missing else "ok",
        "missing": missing,
    }


def _process_status() -> dict[str, Any]:
    return {
        "status": "running",
        "pid": os.getpid(),
    }

def _read_config_json() -> dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
