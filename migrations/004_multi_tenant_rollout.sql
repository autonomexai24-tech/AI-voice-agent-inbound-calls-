ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_tenants_phone_digits
    ON tenants ((regexp_replace(phone_number, '[^0-9]', '', 'g')));

CREATE INDEX IF NOT EXISTS idx_tenants_active_phone_digits
    ON tenants ((regexp_replace(phone_number, '[^0-9]', '', 'g')))
    WHERE is_active = TRUE;
