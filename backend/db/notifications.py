from __future__ import annotations

from typing import Optional
from uuid import UUID

from backend.db.connection import get_connection
from backend.utils.logging import get_logger

logger = get_logger("backend.db.notifications")

VALID_STATUSES = {"pending", "sent", "failed"}


def insert_notification_event(
    *,
    tenant_id: UUID,
    call_id: Optional[str],
    phone_number: str,
    message: str,
    provider: str,
    status: str = "pending",
    error_message: Optional[str] = None,
) -> UUID:
    _validate_status(status)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notification_events (
                    tenant_id, call_id, phone_number, message,
                    provider, status, error_message
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(tenant_id),
                    call_id,
                    phone_number,
                    message,
                    provider,
                    status,
                    error_message,
                ),
            )
            (new_id,) = cur.fetchone()
            return new_id


def update_notification_event_status(
    *,
    tenant_id: UUID,
    notification_id: UUID,
    status: str,
    error_message: Optional[str] = None,
) -> bool:
    _validate_status(status)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE notification_events
                SET status = %s, error_message = %s
                WHERE id = %s AND tenant_id = %s
                """,
                (status, error_message, str(notification_id), str(tenant_id)),
            )
            return cur.rowcount > 0


def fetch_notification_events(
    *,
    tenant_id: UUID,
    limit: int = 100,
    status: Optional[str] = None,
) -> list[dict]:
    if status is not None:
        _validate_status(status)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    """
                    SELECT id, tenant_id, call_id, phone_number, message,
                           provider, status, error_message, created_at
                    FROM notification_events
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), status, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, tenant_id, call_id, phone_number, message,
                           provider, status, error_message, created_at
                    FROM notification_events
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), limit),
                )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "tenant_id": row[1],
                    "call_id": row[2],
                    "phone_number": row[3],
                    "message": row[4],
                    "provider": row[5],
                    "status": row[6],
                    "error_message": row[7],
                    "created_at": row[8],
                }
                for row in rows
            ]


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid notification status: {status}")
