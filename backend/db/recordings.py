from __future__ import annotations

from typing import Optional
from uuid import UUID

from backend.db.connection import get_connection

VALID_UPLOAD_STATUSES = {"pending", "uploaded", "failed"}


def insert_recording(
    *,
    tenant_id: UUID,
    call_id: str,
    storage_key: str,
    recording_url: Optional[str],
    duration_seconds: Optional[int],
    file_size: Optional[int],
    upload_status: str = "pending",
    call_log_id: Optional[UUID] = None,
) -> UUID:
    _validate_status(upload_status)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO call_recordings (
                    tenant_id, call_log_id, call_id, storage_key,
                    recording_url, duration_seconds, file_size, upload_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(tenant_id),
                    str(call_log_id) if call_log_id else None,
                    call_id,
                    storage_key,
                    recording_url,
                    duration_seconds,
                    file_size,
                    upload_status,
                ),
            )
            (new_id,) = cur.fetchone()
            return new_id


def update_recording_status(
    *,
    tenant_id: UUID,
    recording_id: UUID,
    upload_status: str,
    recording_url: Optional[str] = None,
    file_size: Optional[int] = None,
) -> bool:
    _validate_status(upload_status)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE call_recordings
                SET upload_status = %s,
                    recording_url = COALESCE(%s, recording_url),
                    file_size = COALESCE(%s, file_size)
                WHERE id = %s AND tenant_id = %s
                """,
                (upload_status, recording_url, file_size, str(recording_id), str(tenant_id)),
            )
            return cur.rowcount > 0


def get_recording(*, tenant_id: UUID, recording_id: UUID) -> Optional[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, call_log_id, call_id, storage_key,
                       recording_url, duration_seconds, file_size,
                       upload_status, created_at
                FROM call_recordings
                WHERE id = %s AND tenant_id = %s
                """,
                (str(recording_id), str(tenant_id)),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None


def fetch_recordings_for_tenant(*, tenant_id: UUID, limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, call_log_id, call_id, storage_key,
                       recording_url, duration_seconds, file_size,
                       upload_status, created_at
                FROM call_recordings
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (str(tenant_id), limit),
            )
            return [_row_to_dict(row) for row in cur.fetchall()]


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "tenant_id": row[1],
        "call_log_id": row[2],
        "call_id": row[3],
        "storage_key": row[4],
        "recording_url": row[5],
        "duration_seconds": row[6],
        "file_size": row[7],
        "upload_status": row[8],
        "created_at": row[9],
    }


def _validate_status(status: str) -> None:
    if status not in VALID_UPLOAD_STATUSES:
        raise ValueError(f"Invalid recording upload status: {status}")
