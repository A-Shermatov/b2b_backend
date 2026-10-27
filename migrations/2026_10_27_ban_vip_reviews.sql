-- Timed bans, VIP, richer two-way reviews
ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS block_reason TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

ALTER TABLE companies ADD COLUMN IF NOT EXISTS vip BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS vip_until TIMESTAMPTZ;

ALTER TABLE reviews ADD COLUMN IF NOT EXISTS service_rating INTEGER;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS delivery_rating INTEGER;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS quality_rating INTEGER;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

-- Allow one review per reviewer per contract (buyer and supplier each)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'reviews_contract_id_key'
       OR conname = 'reviews_contract_id_reviewer_actor_id_key'
  ) THEN
    NULL;
  END IF;
EXCEPTION WHEN others THEN
  NULL;
END $$;

ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_contract_id_key;
ALTER TABLE reviews DROP CONSTRAINT IF EXISTS uq_reviews_contract;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_reviews_contract_reviewer'
  ) THEN
    ALTER TABLE reviews
      ADD CONSTRAINT uq_reviews_contract_reviewer UNIQUE (contract_id, reviewer_actor_id);
  END IF;
END $$;

COMMENT ON COLUMN users.banned_until IS 'NULL = permanent block when status=blocked; else timed ban end';
COMMENT ON COLUMN companies.vip IS 'Admin-granted VIP badge';
