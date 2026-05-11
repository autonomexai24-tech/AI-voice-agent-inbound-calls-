-- ============================================================================
-- Migration 002 - Clean duplicate tenant slugs and create active slug index
-- ============================================================================
-- Keeps the oldest tenant row for each lowercase slug, moves known tenant-scoped
-- child rows to that keeper, deletes duplicate tenant rows, then creates the
-- unique active-slug index.
-- ============================================================================

BEGIN;

CREATE TEMP TABLE tenant_slug_duplicates ON COMMIT DROP AS
WITH ranked AS (
    SELECT
        id AS duplicate_id,
        first_value(id) OVER (
            PARTITION BY lower(slug)
            ORDER BY created_at ASC, id::text ASC
        ) AS keep_id,
        lower(slug) AS slug_key,
        row_number() OVER (
            PARTITION BY lower(slug)
            ORDER BY created_at ASC, id::text ASC
        ) AS row_number
    FROM tenants
    WHERE NULLIF(slug, '') IS NOT NULL
)
SELECT duplicate_id, keep_id, slug_key
FROM ranked
WHERE row_number > 1;

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['call_logs', 'bookings', 'notification_events', 'call_recordings']
    LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL THEN
            EXECUTE format(
                'UPDATE %I AS child SET tenant_id = d.keep_id FROM tenant_slug_duplicates d WHERE child.tenant_id = d.duplicate_id',
                table_name
            );
        END IF;
    END LOOP;
END $$;

DELETE FROM tenants AS tenant
USING tenant_slug_duplicates AS duplicate
WHERE tenant.id = duplicate.duplicate_id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_slug_lower
ON tenants ((lower(slug)))
WHERE is_active = TRUE;

COMMIT;
