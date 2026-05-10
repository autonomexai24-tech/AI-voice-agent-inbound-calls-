from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from backend.db.connection import is_postgres_enabled
from backend.integrations.sms import (
    Fast2SMSProvider,
    SMSProvider,
    SMSResult,
    normalize_indian_phone,
    render_booking_sms,
)
from backend.utils.formatting import mask_phone
from backend.utils.logging import get_logger

logger = get_logger("backend.services.notification")


class NotificationService:
    def __init__(self, sms_provider: Optional[SMSProvider] = None) -> None:
        self.sms_provider = sms_provider or Fast2SMSProvider()

    async def send_booking_confirmation_sms(
        self,
        *,
        tenant_id: Optional[str | UUID],
        call_id: Optional[str],
        caller_name: str,
        caller_phone: str,
        booking_time_iso: str,
        business_name: str,
        language: Optional[str] = None,
        did: Optional[str] = None,
    ) -> SMSResult:
        try:
            normalized_phone = normalize_indian_phone(caller_phone)
        except ValueError:
            logger.warning(
                "notification.sms.invalid_phone",
                extra={"call_id": call_id, "tenant_id": str(tenant_id or ""), "did": did, "phone_masked": mask_phone(caller_phone)},
            )
            return SMSResult(
                provider=self.sms_provider.name,
                phone_number=caller_phone,
                status="failed",
                error_message="invalid_indian_phone_number",
            )

        message = render_booking_sms(
            caller_name=caller_name,
            business_name=business_name,
            start_time=booking_time_iso,
            language=language,
        )
        tenant_uuid = _coerce_uuid(tenant_id)
        event_id = None

        if tenant_uuid and is_postgres_enabled():
            try:
                from backend.db.notifications import insert_notification_event

                event_id = insert_notification_event(
                    tenant_id=tenant_uuid,
                    call_id=call_id,
                    phone_number=normalized_phone,
                    message=message,
                    provider=self.sms_provider.name,
                    status="pending",
                )
            except Exception as exc:
                logger.warning(
                    "notification.sms.event_insert_failed",
                    extra={
                        "call_id": call_id,
                        "tenant_id": str(tenant_uuid),
                        "did": did,
                        "error_type": type(exc).__name__,
                    },
                )

        result = await self._send_with_retry(normalized_phone, message, call_id=call_id, tenant_id=tenant_uuid, did=did)

        if event_id and tenant_uuid:
            try:
                from backend.db.notifications import update_notification_event_status

                update_notification_event_status(
                    tenant_id=tenant_uuid,
                    notification_id=event_id,
                    status=result.status,
                    error_message=result.error_message,
                )
            except Exception as exc:
                logger.warning(
                    "notification.sms.event_update_failed",
                    extra={
                        "call_id": call_id,
                        "tenant_id": str(tenant_uuid),
                        "did": did,
                        "error_type": type(exc).__name__,
                    },
                )

        logger.info(
            "notification.sms.completed",
            extra={
                "call_id": call_id,
                "tenant_id": str(tenant_uuid or tenant_id or ""),
                "did": did,
                "provider": result.provider,
                "status": result.status,
                "phone_masked": mask_phone(normalized_phone),
            },
        )
        return result

    async def _send_with_retry(
        self,
        phone_number: str,
        message: str,
        *,
        call_id: Optional[str],
        tenant_id: Optional[UUID],
        did: Optional[str],
    ) -> SMSResult:
        result = await self.sms_provider.send_sms(phone_number, message)
        if result.status == "sent" or result.error_message == "missing_fast2sms_api_key":
            return result

        logger.warning(
            "notification.sms.retry",
            extra={
                "call_id": call_id,
                "tenant_id": str(tenant_id or ""),
                "did": did,
                "provider": result.provider,
                "phone_masked": mask_phone(phone_number),
                "error": result.error_message,
            },
        )
        await asyncio.sleep(0.5)
        return await self.sms_provider.send_sms(phone_number, message)


async def send_booking_confirmation_sms(**kwargs) -> SMSResult:
    try:
        return await NotificationService().send_booking_confirmation_sms(**kwargs)
    except Exception as exc:
        logger.warning(
            "notification.sms.safe_failure",
            extra={"call_id": kwargs.get("call_id"), "did": kwargs.get("did"), "error_type": type(exc).__name__},
        )
        return SMSResult(
            provider="fast2sms",
            phone_number=str(kwargs.get("caller_phone") or ""),
            status="failed",
            error_message=type(exc).__name__,
        )


def _coerce_uuid(value: Optional[str | UUID]) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
