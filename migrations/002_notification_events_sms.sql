BEGIN;

CREATE TABLE IF NOT EXISTS notification_events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id        TEXT,
    phone_number   TEXT NOT NULL,
    message        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    error_message  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS call_id TEXT;
ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS phone_number TEXT;
ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS message TEXT;
ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending';
ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE notification_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notification_events' AND column_name = 'recipient'
    ) THEN
        UPDATE notification_events
        SET phone_number = COALESCE(phone_number, recipient, '')
        WHERE phone_number IS NULL;
    ELSE
        UPDATE notification_events
        SET phone_number = COALESCE(phone_number, '')
        WHERE phone_number IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'notification_events' AND column_name = 'channel'
    ) THEN
        UPDATE notification_events
        SET provider = COALESCE(provider, channel, 'unknown')
        WHERE provider IS NULL;
    ELSE
        UPDATE notification_events
        SET provider = COALESCE(provider, 'unknown')
        WHERE provider IS NULL;
    END IF;
END $$;

UPDATE notification_events
SET message = COALESCE(message, '')
WHERE message IS NULL;

UPDATE notification_events
SET status = 'pending'
WHERE status IS NULL OR status NOT IN ('pending', 'sent', 'failed');

ALTER TABLE notification_events ALTER COLUMN phone_number SET NOT NULL;
ALTER TABLE notification_events ALTER COLUMN message SET NOT NULL;
ALTER TABLE notification_events ALTER COLUMN provider SET NOT NULL;
ALTER TABLE notification_events ALTER COLUMN status SET NOT NULL;
ALTER TABLE notification_events ALTER COLUMN created_at SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notification_events_status_check'
    ) THEN
        ALTER TABLE notification_events
        ADD CONSTRAINT notification_events_status_check
        CHECK (status IN ('pending', 'sent', 'failed'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_notification_events_tenant_created
    ON notification_events (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_events_call_id
    ON notification_events (call_id);

COMMIT;
