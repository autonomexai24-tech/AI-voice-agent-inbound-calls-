"""Dataclass mirrors of the PostgreSQL schema.

Each class corresponds 1:1 to a table in `migrations/001_initial.sql`.
Field names and types match the SQL columns. No business logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID


@dataclass(frozen=True)
class Tenant:
    id: UUID
    name: str
    slug: str
    phone_number: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class User:
    id: UUID
    tenant_id: UUID
    email: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True)
class TenantConfig:
    id: UUID
    tenant_id: UUID
    agent_instructions: Optional[str]
    first_line: Optional[str]
    tts_voice: str
    tts_language: str
    lang_preset: str
    llm_model: str
    endpointing_delay: float
    business_hours_json: Optional[Any]
    transfer_number: Optional[str]
    cal_api_key: Optional[str]
    cal_event_type_id: Optional[str]
    updated_at: datetime


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
