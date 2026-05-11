-- ============================================================================
-- Migration 001 - Simple inbound voice MVP schema
-- ============================================================================
-- The production call path needs one table only: tenants.
-- Each inbound SIP DID maps directly to one tenants row. The worker reads
-- prompt, greeting, language, and voice from this row at call start.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL UNIQUE,
    system_prompt TEXT NOT NULL DEFAULT '',
    welcome_message TEXT NOT NULL DEFAULT '',
    languages TEXT NOT NULL DEFAULT 'multilingual',
    voice TEXT NOT NULL DEFAULT 'kavya',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_phone_digits
    ON tenants ((regexp_replace(phone_number, '[^0-9]', '', 'g')));

CREATE INDEX IF NOT EXISTS idx_tenants_active_phone_digits
    ON tenants ((regexp_replace(phone_number, '[^0-9]', '', 'g')))
    WHERE is_active = TRUE;

COMMIT;
