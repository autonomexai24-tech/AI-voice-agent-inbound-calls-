from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from backend.db.connection import is_postgres_enabled
from backend.integrations.storage import S3StorageProvider, cleanup_local_file
from backend.utils.logging import get_logger

logger = get_logger("backend.services.recording")


@dataclass(frozen=True)
class RecordingMetadata:
    id: UUID
    storage_key: str
    upload_status: str


class RecordingService:
    def __init__(self, storage: Optional[S3StorageProvider] = None) -> None:
        self.storage = storage or S3StorageProvider()

    async def record_livekit_upload(
        self,
        *,
        tenant_id: Optional[str | UUID],
        call_log_id: Optional[str | UUID],
        call_id: str,
        storage_key: str,
        duration_seconds: Optional[int],
        file_size: Optional[int],
        upload_status: str,
    ) -> Optional[RecordingMetadata]:
        tenant_uuid = _coerce_uuid(tenant_id)
        call_log_uuid = _coerce_uuid(call_log_id)
        if not tenant_uuid or not is_postgres_enabled():
            logger.info(
                "recording.metadata.skipped",
                extra={"tenant_id": str(tenant_id or ""), "call_id": call_id, "reason": "postgres_disabled_or_no_tenant"},
            )
            return None

        try:
            object_url = self.storage.object_url(storage_key) if self.storage.configured else None
            from backend.db.recordings import insert_recording

            recording_id = await asyncio.to_thread(
                insert_recording,
                tenant_id=tenant_uuid,
                call_log_id=call_log_uuid,
                call_id=call_id,
                storage_key=storage_key,
                recording_url=object_url,
                duration_seconds=duration_seconds,
                file_size=file_size,
                upload_status=upload_status,
            )
            logger.info(
                "recording.metadata.saved",
                extra={
                    "tenant_id": str(tenant_uuid),
                    "call_id": call_id,
                    "recording_id": str(recording_id),
                    "upload_status": upload_status,
                },
            )
            return RecordingMetadata(id=recording_id, storage_key=storage_key, upload_status=upload_status)
        except Exception as exc:
            logger.warning(
                "recording.metadata.failed",
                extra={"tenant_id": str(tenant_uuid), "call_id": call_id, "error_type": type(exc).__name__},
            )
            return None

    async def upload_local_recording(
        self,
        *,
        tenant_id: Optional[str | UUID],
        call_log_id: Optional[str | UUID],
        call_id: str,
        local_path: str,
        storage_key: str,
        duration_seconds: Optional[int],
        cleanup_on_success: bool = True,
    ) -> Optional[RecordingMetadata]:
        tenant_uuid = _coerce_uuid(tenant_id)
        call_log_uuid = _coerce_uuid(call_log_id)
        if not tenant_uuid or not is_postgres_enabled():
            logger.info(
                "recording.local_upload.skipped",
                extra={"tenant_id": str(tenant_id or ""), "call_id": call_id, "reason": "postgres_disabled_or_no_tenant"},
            )
            return None

        recording_id = None
        try:
            from backend.db.recordings import insert_recording, update_recording_status

            recording_id = await asyncio.to_thread(
                insert_recording,
                tenant_id=tenant_uuid,
                call_log_id=call_log_uuid,
                call_id=call_id,
                storage_key=storage_key,
                recording_url=self.storage.object_url(storage_key) if self.storage.configured else None,
                duration_seconds=duration_seconds,
                file_size=None,
                upload_status="pending",
            )
            result = await self.storage.upload_file(local_path=local_path, storage_key=storage_key, retries=1)
            await asyncio.to_thread(
                update_recording_status,
                tenant_id=tenant_uuid,
                recording_id=recording_id,
                upload_status=result.status,
                file_size=result.file_size,
                recording_url=self.storage.object_url(storage_key) if result.status == "uploaded" else None,
            )
            if result.status == "uploaded" and cleanup_on_success:
                cleanup_local_file(local_path)
            return RecordingMetadata(id=recording_id, storage_key=storage_key, upload_status=result.status)
        except Exception as exc:
            logger.warning(
                "recording.local_upload.failed",
                extra={"tenant_id": str(tenant_uuid), "call_id": call_id, "error_type": type(exc).__name__},
            )
            if recording_id:
                try:
                    from backend.db.recordings import update_recording_status

                    await asyncio.to_thread(
                        update_recording_status,
                        tenant_id=tenant_uuid,
                        recording_id=recording_id,
                        upload_status="failed",
                    )
                except Exception:
                    pass
            return None

    async def signed_playback_url(
        self,
        *,
        tenant_id: UUID,
        recording_id: UUID,
        expires_seconds: int = 3600,
    ) -> Optional[str]:
        try:
            from backend.db.recordings import get_recording

            row = await asyncio.to_thread(get_recording, tenant_id=tenant_id, recording_id=recording_id)
            if not row or row.get("upload_status") != "uploaded":
                return None
            return self.storage.generate_signed_url(row["storage_key"], expires_seconds=expires_seconds)
        except Exception as exc:
            logger.warning(
                "recording.signed_url.failed",
                extra={"tenant_id": str(tenant_id), "recording_id": str(recording_id), "error_type": type(exc).__name__},
            )
            return None


async def record_livekit_upload_metadata(**kwargs) -> Optional[RecordingMetadata]:
    try:
        return await RecordingService().record_livekit_upload(**kwargs)
    except Exception as exc:
        logger.warning("recording.safe_failure", extra={"call_id": kwargs.get("call_id"), "error_type": type(exc).__name__})
        return None


def _coerce_uuid(value: Optional[str | UUID]) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
