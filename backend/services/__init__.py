"""Service layer (Phase 2 scaffolding).

Services are thin, stateless wrappers around backend.db.* and future
provider integrations. They exist to keep the eventual API/voice layers
free of raw SQL and vendor-specific calls.

Phase 2 rule: thin only. No business logic explosion, no orchestration
complexity, no caches, no queues. Each service is roughly a facade.
"""

from backend.services.tenant_service import TenantService
from backend.services.booking_service import BookingService
from backend.services.notification_service import NotificationService, send_booking_confirmation_sms
from backend.services.recording_service import RecordingService

__all__ = [
    "TenantService",
    "BookingService",
    "NotificationService",
    "send_booking_confirmation_sms",
    "RecordingService",
]
