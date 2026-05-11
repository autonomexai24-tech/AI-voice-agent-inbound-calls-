"""Dataclass mirrors of the PostgreSQL schema.

The inbound voice MVP stores tenant runtime configuration directly on the
`tenants` table so a new call can load prompt, greeting, language, and
voice with one indexed DID lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(frozen=True)
class Tenant:
    id: UUID
    name: str
    phone_number: str
    system_prompt: str
    welcome_message: str
    languages: str
    voice: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class CallLog:
    id: UUID
    tenant_id: UUID
    phone_number: Optional[str]
    duration_seconds: Optional[int]
    transcript: Optional[str]
    summary: Optional[str]
    sentiment: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class Booking:
    id: UUID
    tenant_id: UUID
    call_log_id: Optional[UUID]
    patient_name: Optional[str]
    patient_phone: Optional[str]
    start_time: Optional[datetime]
    status: str
    cal_booking_uid: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class NotificationEvent:
    id: UUID
    tenant_id: UUID
    call_id: Optional[str]
    phone_number: str
    message: str
    provider: str
    status: str
    error_message: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class CallRecording:
    id: UUID
    tenant_id: UUID
    call_log_id: Optional[UUID]
    call_id: str
    storage_key: str
    recording_url: Optional[str]
    duration_seconds: Optional[int]
    file_size: Optional[int]
    upload_status: str
    created_at: datetime
