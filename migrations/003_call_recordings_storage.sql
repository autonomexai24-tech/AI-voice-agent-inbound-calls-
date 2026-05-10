BEGIN;

ALTER TABLE call_recordings ADD COLUMN IF NOT EXISTS call_id TEXT;
ALTER TABLE call_recordings ADD COLUMN IF NOT EXISTS recording_url TEXT;
ALTER TABLE call_recordings ADD COLUMN IF NOT EXISTS file_size BIGINT;
ALTER TABLE call_recordings ADD COLUMN IF NOT EXISTS upload_status TEXT DEFAULT 'pending';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'call_recordings' AND column_name = 'size_bytes'
    ) THEN
        UPDATE call_recordings
        SET file_size = COALESCE(file_size, size_bytes)
        WHERE file_size IS NULL;
    END IF;
END $$;

UPDATE call_recordings
SET call_id = COALESCE(call_id, call_log_id::text, id::text)
WHERE call_id IS NULL;

UPDATE call_recordings
SET upload_status = 'pending'
WHERE upload_status IS NULL OR upload_status NOT IN ('pending', 'uploaded', 'failed');

ALTER TABLE call_recordings ALTER COLUMN call_id SET NOT NULL;
ALTER TABLE call_recordings ALTER COLUMN upload_status SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'call_recordings_upload_status_check'
    ) THEN
        ALTER TABLE call_recordings
        ADD CONSTRAINT call_recordings_upload_status_check
        CHECK (upload_status IN ('pending', 'uploaded', 'failed'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_call_recordings_call_id
    ON call_recordings (call_id);

COMMIT;
