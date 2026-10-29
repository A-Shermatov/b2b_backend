-- Track last modification time of catalog items (admin catalog sorting / display)

ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE catalog_items SET updated_at = created_at WHERE updated_at IS NULL;
ALTER TABLE catalog_items ALTER COLUMN updated_at SET DEFAULT NOW();

CREATE OR REPLACE FUNCTION catalog_items_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_catalog_items_updated_at'
          AND tgrelid = 'catalog_items'::regclass
    ) THEN
        CREATE TRIGGER trg_catalog_items_updated_at
        BEFORE UPDATE ON catalog_items
        FOR EACH ROW EXECUTE FUNCTION catalog_items_touch_updated_at();
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_catalog_items_status_created ON catalog_items (status, created_at DESC);
