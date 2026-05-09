"""Call recording metadata facade (Phase 2 scaffolding).

Per ARCHITECTURE.md §10, recordings are uploaded asynchronously to
S3-compatible storage post-call. Metadata lives in the `call_recordings`
PostgreSQL table.

Phase 2 scope: metadata-layer facade only. Actual upload integration
(LiveKit Egress → S3-compatible storage) is Phase 6. The current agent.py
still writes recordings to Supabase S3 directly; that code is NOT touched
in Phase 2.

Design:
  * StorageReference — generic, vendor-neutral handle.
  * RecordingService — record new metadata, list per tenant.
  * Uploader (Protocol) — future provider interface; not required yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol
from uuid import UUID

from backend.db.connection import get_connection
from backend.utils.logging import get_logger

logger = get_logger("backend.services.recording")


@dataclass(frozen=True)
class StorageReference:
    """Abstract reference to a stored recording object.

    `storage_key` is opaque to callers; its meaning depends on the
    provider (e.g. S3 object key, MinIO path). Never a secret on its own.
    """

    storage_key: str
    size_bytes: Optional[int] = None
    duration_seconds: Optional[int] = None


class Uploader(Protocol):
    """Future interface for S3-compatible uploaders. Not required in Phase 2."""

    name: str

    async def upload(self, local_path: str, object_key: str) -> StorageReference: ...


class RecordingService:
    """Persists call recording metadata. Upload logic is Phase 6."""

    def record_metadata(
        self,
        tenant_id: UUID,
        call_log_id: Optional[UUID],
        reference: StorageReference,
    ) -> UUID:
        """Insert a call_recordings row. Returns the new id."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO call_recordings (
                        tenant_id, call_log_id, storage_key,
                        duration_seconds, size_bytes
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        str(tenant_id),
                        str(call_log_id) if call_log_id else None,
                        reference.storage_key,
                        reference.duration_seconds,
                        reference.size_bytes,
                    ),
                )
                (new_id,) = cur.fetchone()

        logger.info(
            "recording.metadata.saved",
            extra={
                "tenant_id": str(tenant_id),
                "recording_id": str(new_id),
                "has_call_log": call_log_id is not None,
            },
        )
        return new_id

    def list_for_tenant(self, tenant_id: UUID, limit: int = 200) -> list[dict]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, tenant_id, call_log_id, storage_key,
                           duration_seconds, size_bytes, created_at
                    FROM call_recordings
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), limit),
                )
                rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "tenant_id": r[1],
                "call_log_id": r[2],
                "storage_key": r[3],
                "duration_seconds": r[4],
                "size_bytes": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]
