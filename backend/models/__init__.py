"""Typed domain entities.

Plain dataclasses mirroring the PostgreSQL tables used by the app. No
Pydantic, no ORM — pure standard-library dataclasses avoid coupling the
voice path to a validation library.

These types are reference shapes for future typed API layers. The
backend.db.* functions currently return dict[str, Any] for pragmatic
reasons; conversion helpers (if needed) will be added in Phase 4.
"""

from backend.models.entities import (
    Booking,
    CallLog,
    CallRecording,
    NotificationEvent,
    Tenant,
)

__all__ = [
    "Tenant",
    "CallLog",
    "Booking",
    "NotificationEvent",
    "CallRecording",
]
