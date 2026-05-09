"""Booking CRUD (raw SQL, tenant-scoped).

Per ARCHITECTURE.md §4, the bookings table:
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    call_log_id UUID REFERENCES call_logs(id),
    patient_name TEXT,
    patient_phone TEXT,
    start_time TIMESTAMPTZ,
    status TEXT DEFAULT 'confirmed',
    cal_booking_uid TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from backend.db.connection import get_connection
from backend.utils.logging import get_logger

logger = get_logger("backend.db.bookings")


def insert_booking(
    tenant_id: UUID,
    call_log_id: Optional[UUID],
    patient_name: str,
    patient_phone: str,
    start_time: datetime,
    cal_booking_uid: Optional[str] = None,
    status: str = "confirmed",
) -> UUID:
    """Insert a booking row. Returns the new id."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bookings (
                    tenant_id, call_log_id, patient_name, patient_phone,
                    start_time, status, cal_booking_uid
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(tenant_id),
                    str(call_log_id) if call_log_id else None,
                    patient_name,
                    patient_phone,
                    start_time,
                    status,
                    cal_booking_uid,
                ),
            )
            (new_id,) = cur.fetchone()
            return new_id


def fetch_bookings(
    tenant_id: UUID,
    limit: int = 200,
    status: Optional[str] = None,
) -> list[dict]:
    """List a tenant's bookings, newest first. Optional status filter."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if status is None:
                cur.execute(
                    """
                    SELECT id, tenant_id, call_log_id, patient_name,
                           patient_phone, start_time, status,
                           cal_booking_uid, created_at
                    FROM bookings
                    WHERE tenant_id = %s
                    ORDER BY start_time DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, tenant_id, call_log_id, patient_name,
                           patient_phone, start_time, status,
                           cal_booking_uid, created_at
                    FROM bookings
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY start_time DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), status, limit),
                )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "tenant_id": r[1],
                    "call_log_id": r[2],
                    "patient_name": r[3],
                    "patient_phone": r[4],
                    "start_time": r[5],
                    "status": r[6],
                    "cal_booking_uid": r[7],
                    "created_at": r[8],
                }
                for r in rows
            ]


def update_booking_status(
    tenant_id: UUID,
    booking_id: UUID,
    status: str,
) -> bool:
    """Update a booking's status. Returns True if a row was affected."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bookings
                SET status = %s
                WHERE id = %s AND tenant_id = %s
                """,
                (status, str(booking_id), str(tenant_id)),
            )
            return cur.rowcount > 0
