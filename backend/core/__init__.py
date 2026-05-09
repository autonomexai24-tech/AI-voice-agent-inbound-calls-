"""Core cross-cutting backend concerns: config, health, startup hooks.

This package consolidates environment validation, health aggregation, and
startup safety checks. Phase 1 left env validation in `backend/config/`;
Phase 2 adds `backend.core.config` as the preferred access point going
forward. `backend/config/` remains importable temporarily for compatibility.
"""

from backend.core.config import AppConfig, load_app_config
from backend.core.health import aggregate_health
from backend.core.startup import run_startup_checks

__all__ = [
    "AppConfig",
    "load_app_config",
    "aggregate_health",
    "run_startup_checks",
]
