"""Booking persistence facade (Phase 2 scaffolding).

Thin wrapper over `backend.db.bookings`. Cal.com API integration continues
to live in root-level `calendar_tools.py` for now; Phase 3/5 will move
the HTTP client into `backend/integrations/calendar.py` and wire it in
here.

Phase 2 rule: no redesign of the booking flow. This facade exposes the
same three primitives the existing flow needs: record a new booking,
list a tenant's bookings, update booking status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from backend.db import bookings as booking_repo
from backend.utils.logging import get_logger

logger = get_logger("backend.services.booking")


class BookingService:
    """Facade for booking persistence."""

    def record_booking(
        self,
        tenant_id: UUID,
        call_log_id: Optional[UUID],
        patient_name: str,
        patient_phone: str,
        start_time: datetime,
        cal_booking_uid: Optional[str] = None,
        status: str = "confirmed",
    ) -> UUID:
        """Persist a booking. Returns the new booking id."""
        booking_id = booking_repo.insert_booking(
            tenant_id=tenant_id,
            call_log_id=call_log_id,
            patient_name=patient_name,
            patient_phone=patient_phone,
            start_time=start_time,
            cal_booking_uid=cal_booking_uid,
            status=status,
        )
        logger.info(
            "booking.created",
            extra={
                "tenant_id": str(tenant_id),
                "booking_id": str(booking_id),
                "status": status,
            },
        )
        return booking_id

    def list_bookings(
        self,
        tenant_id: UUID,
        limit: int = 200,
        status: Optional[str] = None,
    ) -> list[dict]:
        return booking_repo.fetch_bookings(tenant_id, limit=limit, status=status)

    def set_status(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        status: str,
    ) -> bool:
        changed = booking_repo.update_booking_status(tenant_id, booking_id, status)
        if changed:
            logger.info(
                "booking.status_updated",
                extra={
                    "tenant_id": str(tenant_id),
                    "booking_id": str(booking_id),
                    "status": status,
                },
            )
        return changed
