-- ============================================================================
-- Migration 001 — Initial PostgreSQL schema
-- ============================================================================
-- Target: PostgreSQL 14+ (EasyPanel-managed)
-- Scope:  Multi-tenant core tables defined in docs/ARCHITECTURE.md §4.
--
-- Forward-only. Every CREATE below has a corresponding DROP documented at
-- the bottom of this file for rollback reference.
--
-- Rules enforced here (per docs/RULES.md):
--   * UUID primary keys
--   * tenant_id on every tenant-scoped table, with FK to tenants(id)
--   * created_at / updated_at timestamps where appropriate
--   * Indexes sized for low-latency tenant-scoped queries
--
-- Phase 1 scope:
--   * Core identity and runtime tables (tenants, users, tenant_config)
--   * Voice pipeline audit tables (call_logs, bookings, notification_events,
--     call_recordings)
--
-- Out of scope (deliberately excluded until later phases):
--   * Billing / subscription tables
--   * Analytics aggregation tables
--   * Outbound campaign tables
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ----------------------------------------------------------------------------
-- tenants
-- One row per business. phone_number is the Vobiz DID used to resolve
-- incoming calls to a tenant at room-join time.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    phone_number  TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lookups by DID happen on every inbound call; slug is for admin/UI routing.
CREATE INDEX IF NOT EXISTS idx_tenants_phone_number ON tenants (phone_number);
CREATE INDEX IF NOT EXISTS idx_tenants_slug         ON tenants (slug);


-- ----------------------------------------------------------------------------
-- users
-- Dashboard login identities. One tenant may have many users. Email is
-- unique within a tenant (same email can exist across tenants if needed).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT users_email_per_tenant_unique UNIQUE (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email     ON users (email);


-- ----------------------------------------------------------------------------
-- tenant_config
-- Per-tenant AI personality. Loaded once at call start. Edits made via the
-- dashboard are applied on the next inbound call (no restart, no cache).
-- Exactly one row per tenant (enforced by UNIQUE on tenant_id).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_config (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    agent_instructions   TEXT,
    first_line           TEXT,
    tts_voice            TEXT        NOT NULL DEFAULT 'kavya',
    tts_language         TEXT        NOT NULL DEFAULT 'hi-IN',
    lang_preset          TEXT        NOT NULL DEFAULT 'multilingual',
    llm_model            TEXT        NOT NULL DEFAULT 'gpt-4o-mini',
    endpointing_delay    REAL        NOT NULL DEFAULT 0.5,
    business_hours_json  JSONB,
    transfer_number      TEXT,
    cal_api_key          TEXT,
    cal_event_type_id    TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ----------------------------------------------------------------------------
-- call_logs
-- Audit row per completed call. Inserted in the async post-call pipeline.
-- Dashboard queries pull the newest first scoped by tenant_id.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_logs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number      TEXT,
    duration_seconds  INTEGER,
    transcript        TEXT,
    summary           TEXT,
    sentiment         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Composite index matches the dominant dashboard query:
--   SELECT ... FROM call_logs WHERE tenant_id = $1 ORDER BY created_at DESC
CREATE INDEX IF NOT EXISTS idx_call_logs_tenant_created
    ON call_logs (tenant_id, created_at DESC);


-- ----------------------------------------------------------------------------
-- bookings
-- Appointment records. Linked to the call that produced them (nullable so
-- future direct-API bookings don't need a synthetic call_log row).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bookings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_log_id       UUID REFERENCES call_logs(id) ON DELETE SET NULL,
    patient_name      TEXT,
    patient_phone     TEXT,
    start_time        TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'confirmed',
    cal_booking_uid   TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bookings_tenant_start
    ON bookings (tenant_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_bookings_call_log
    ON bookings (call_log_id);


-- ----------------------------------------------------------------------------
-- notification_events
-- Audit trail for SMS and other notifications. Tolerant of missing call_log
-- (e.g. future admin-triggered messages).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_log_id  UUID REFERENCES call_logs(id) ON DELETE SET NULL,
    channel      TEXT NOT NULL,       -- 'sms', 'webhook', ...
    recipient    TEXT,
    status       TEXT,                -- 'sent', 'failed', 'pending'
    sent_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notification_events_tenant
    ON notification_events (tenant_id);
CREATE INDEX IF NOT EXISTS idx_notification_events_call_log
    ON notification_events (call_log_id);


-- ----------------------------------------------------------------------------
-- call_recordings
-- Metadata only. Audio lives in S3-compatible object storage; storage_key is
-- the provider-agnostic reference. Upload is always async and post-call.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_recordings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_log_id       UUID REFERENCES call_logs(id) ON DELETE CASCADE,
    storage_key       TEXT NOT NULL,
    duration_seconds  INTEGER,
    size_bytes        BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_recordings_tenant
    ON call_recordings (tenant_id);
CREATE INDEX IF NOT EXISTS idx_call_recordings_call_log
    ON call_recordings (call_log_id);

COMMIT;

-- ============================================================================
-- Rollback (reference only; run manually if a revert is needed)
-- ============================================================================
-- BEGIN;
--   DROP TABLE IF EXISTS call_recordings;
--   DROP TABLE IF EXISTS notification_events;
--   DROP TABLE IF EXISTS bookings;
--   DROP TABLE IF EXISTS call_logs;
--   DROP TABLE IF EXISTS tenant_config;
--   DROP TABLE IF EXISTS users;
--   DROP TABLE IF EXISTS tenants;
-- COMMIT;
