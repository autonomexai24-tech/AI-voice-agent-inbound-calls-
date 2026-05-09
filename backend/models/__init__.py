"""Typed domain entities (Phase 2 scaffolding).

Plain dataclasses mirroring the PostgreSQL schema in ARCHITECTURE.md §4.
No Pydantic, no ORM — pure standard-library dataclasses keep Phase 2
dependency-free and avoid coupling the voice path to a validation lib.

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
    TenantConfig,
    User,
)

__all__ = [
    "Tenant",
    "User",
    "TenantConfig",
    "CallLog",
    "Booking",
    "NotificationEvent",
    "CallRecording",
]
