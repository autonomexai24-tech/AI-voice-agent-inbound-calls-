"""Provider-abstract notification layer (Phase 2 scaffolding).

Per ARCHITECTURE.md §7 and RULES.md §8, the notification layer is
provider-abstract. Fast2SMS is the default SMS provider, but the
integration surface here must not be coupled to any specific vendor.

Phase 2 scope: define the abstraction + an in-memory audit recorder
using the `notification_events`-style fields. A real provider adapter
(Fast2SMS) is deferred to Phase 5. The existing Telegram/WhatsApp code in
root-level `notify.py` is not touched.

Design:
  * NotificationChannel — enum-like string set.
  * NotificationResult — structured outcome, safe to log.
  * SMSProvider (Protocol) — the interface new providers must satisfy.
  * NotificationService — orchestrator that calls a provider and
    returns a NotificationResult. Does not write to the DB yet; that
    wiring is Phase 5 (notification_events table).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from backend.utils.logging import get_logger

logger = get_logger("backend.services.notification")


class NotificationChannel:
    """Allowed notification channels. Matches notification_events.channel."""

    SMS = "sms"
    WEBHOOK = "webhook"


@dataclass(frozen=True)
class NotificationResult:
    """Outcome of a notification send attempt.

    `status` mirrors notification_events.status: 'sent' | 'failed' | 'pending'.
    `provider_message_id` is optional (vendor-specific), e.g. Fast2SMS job id.
    """

    channel: str
    recipient: str
    status: str
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


class SMSProvider(Protocol):
    """Interface every SMS provider must implement.

    Implementations live in backend/integrations/ (future). They must
    never raise; failures must be returned via `NotificationResult`.
    """

    name: str

    def send(self, to_phone: str, body: str) -> NotificationResult: ...


class NotificationService:
    """Sends patient-facing notifications via a configured provider."""

    def __init__(self, sms_provider: Optional[SMSProvider] = None) -> None:
        self._sms = sms_provider

    def send_sms(self, to_phone: str, body: str) -> NotificationResult:
        """Send an SMS. Returns a structured result; never raises."""
        if self._sms is None:
            # Phase 2: no provider wired. Record an explicit 'pending'
            # outcome so callers and tests behave deterministically.
            logger.warning(
                "notification.sms.no_provider",
                extra={"channel": NotificationChannel.SMS},
            )
            return NotificationResult(
                channel=NotificationChannel.SMS,
                recipient=to_phone,
                status="pending",
                error="no_provider_configured",
            )

        try:
            result = self._sms.send(to_phone=to_phone, body=body)
            logger.info(
                "notification.sms.result",
                extra={
                    "channel": result.channel,
                    "status": result.status,
                    "provider": self._sms.name,
                },
            )
            return result
        except Exception as e:  # noqa: BLE001 — never let SMS fail a call
            logger.error(
                "notification.sms.exception",
                extra={"error": str(e)[:200]},
            )
            return NotificationResult(
                channel=NotificationChannel.SMS,
                recipient=to_phone,
                status="failed",
                error=str(e)[:200],
            )
