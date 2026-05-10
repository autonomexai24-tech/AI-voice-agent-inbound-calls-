"""Disabled legacy persistence adapter.

The production runtime uses backend.db.* PostgreSQL repositories. This module
is kept only so old fallback call sites degrade without external persistence
imports or crashing a live call when PostgreSQL is intentionally disabled.
"""

from __future__ import annotations

from backend.utils.formatting import mask_phone
from backend.utils.logging import get_logger

logger = get_logger("legacy.db")


def save_call_log(
    phone: str,
    duration: int,
    transcript: str,
    summary: str = "",
    recording_url: str = "",
    caller_name: str = "",
    sentiment: str = "unknown",
    estimated_cost_usd: float | None = None,
    call_date: str | None = None,
    call_hour: int | None = None,
    call_day_of_week: str | None = None,
    was_booked: bool = False,
    interrupt_count: int = 0,
) -> dict:
    logger.warning(
        "legacy.db.call_log_skipped",
        extra={"phone_masked": mask_phone(phone), "duration_seconds": duration},
    )
    return {"success": False, "message": "legacy_db_disabled"}


def fetch_call_logs(limit: int = 50) -> list:
    return []


def fetch_bookings() -> list:
    return []


def fetch_stats() -> dict:
    return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}
