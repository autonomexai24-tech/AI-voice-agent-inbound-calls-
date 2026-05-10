"""Call log CRUD (raw SQL, tenant-scoped).

Per ARCHITECTURE.md §4, the call_logs table:
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    phone_number TEXT,
    duration_seconds INTEGER,
    transcript TEXT,
    summary TEXT,
    sentiment TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()

Every query filters by tenant_id. Writes always include tenant_id.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from backend.db.connection import get_connection
from backend.utils.logging import get_logger

logger = get_logger("backend.db.call_logs")


def insert_call_log(
    tenant_id: UUID,
    phone_number: str,
    duration_seconds: int,
    transcript: str,
    summary: str = "",
    sentiment: str = "unknown",
) -> UUID:
    """Insert a call log row and return its generated id."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO call_logs (
                    tenant_id, phone_number, duration_seconds,
                    transcript, summary, sentiment
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(tenant_id),
                    phone_number,
                    duration_seconds,
                    transcript,
                    summary,
                    sentiment,
                ),
            )
            (new_id,) = cur.fetchone()
            return new_id


def fetch_call_logs(tenant_id: UUID, limit: int = 50, offset: int = 0) -> list[dict]:
    """Return the most recent call logs for a tenant, newest first."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, tenant_id, phone_number, duration_seconds,
                    transcript, summary, sentiment, created_at,
                    recording_id, recording_upload_status
                FROM (
                    SELECT
                        cl.id, cl.tenant_id, cl.phone_number, cl.duration_seconds,
                        cl.transcript, cl.summary, cl.sentiment, cl.created_at,
                        cr.id AS recording_id,
                        cr.upload_status AS recording_upload_status
                    FROM call_logs cl
                    LEFT JOIN LATERAL (
                        SELECT id, upload_status
                        FROM call_recordings
                        WHERE tenant_id = cl.tenant_id
                          AND call_log_id = cl.id
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) cr ON TRUE
                    WHERE cl.tenant_id = %s
                ) rows
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (str(tenant_id), limit, offset),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "tenant_id": r[1],
                    "phone_number": r[2],
                    "duration_seconds": r[3],
                    "transcript": r[4],
                    "summary": r[5],
                    "sentiment": r[6],
                    "created_at": r[7],
                    "recording_id": r[8],
                    "recording_upload_status": r[9],
                }
                for r in rows
            ]


def get_call_log(tenant_id: UUID, call_log_id: UUID) -> Optional[dict]:
    """Return a single call log if it belongs to the given tenant."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, tenant_id, phone_number, duration_seconds,
                    transcript, summary, sentiment, created_at
                FROM call_logs
                WHERE id = %s AND tenant_id = %s
                """,
                (str(call_log_id), str(tenant_id)),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "tenant_id": row[1],
                "phone_number": row[2],
                "duration_seconds": row[3],
                "transcript": row[4],
                "summary": row[5],
                "sentiment": row[6],
                "created_at": row[7],
            }


def get_latest_call_for_phone(tenant_id: UUID, phone_number: str) -> Optional[dict]:
    """Return the latest call for a caller within one tenant only."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, phone_number, summary, created_at
                FROM call_logs
                WHERE tenant_id = %s AND phone_number = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(tenant_id), phone_number),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "tenant_id": row[1],
                "phone_number": row[2],
                "summary": row[3],
                "created_at": row[4],
            }


def fetch_call_stats(tenant_id: UUID) -> dict:
    """Aggregate stats for a tenant's dashboard.

    Returns total_calls, total_bookings (linked), avg_duration, booking_rate.
    Bookings are counted via JOIN, not string-matching on summaries.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(cl.id) AS total_calls,
                    COALESCE(AVG(cl.duration_seconds), 0) AS avg_duration,
                    (
                        SELECT COUNT(*) FROM bookings b
                        WHERE b.tenant_id = %s
                    ) AS total_bookings
                FROM call_logs cl
                WHERE cl.tenant_id = %s
                """,
                (str(tenant_id), str(tenant_id)),
            )
            row = cur.fetchone()
            total_calls = int(row[0] or 0)
            avg_duration = round(float(row[1] or 0))
            total_bookings = int(row[2] or 0)
            booking_rate = (
                round((total_bookings / total_calls) * 100) if total_calls else 0
            )
            return {
                "total_calls": total_calls,
                "total_bookings": total_bookings,
                "avg_duration": avg_duration,
                "booking_rate": booking_rate,
            }
