-- Subscription Alif orders, company change requests, review photos/helpful votes

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum
        JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
        WHERE pg_type.typname = 'platform_payment_gateway'
          AND pg_enum.enumlabel = 'alif'
    ) THEN
        ALTER TYPE platform_payment_gateway ADD VALUE 'alif';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS subscription_payment_orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL UNIQUE,
    gateway VARCHAR(20) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plan VARCHAR(20) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'TJS',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    external_transaction_id VARCHAR(64),
    payment_url TEXT,
    raw_callback JSONB,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_subscription_payment_orders_user_id
ON subscription_payment_orders (user_id);

CREATE TABLE IF NOT EXISTS company_change_requests (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    requested_by_user_id INTEGER NOT NULL REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    proposed JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason_from_user TEXT,
    admin_reason TEXT,
    reviewed_by_user_id INTEGER REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_change_requests_one_pending
ON company_change_requests (company_id)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_company_change_requests_status
ON company_change_requests (status, created_at DESC);

CREATE TABLE IF NOT EXISTS review_media (
    id SERIAL PRIMARY KEY,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    file_name VARCHAR(255) NOT NULL,
    file_url VARCHAR(500) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_review_media_review_id ON review_media (review_id);

CREATE TABLE IF NOT EXISTS review_helpful_votes (
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    voter_actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (review_id, voter_actor_id)
);

ALTER TABLE reviews ADD COLUMN IF NOT EXISTS helpful_count INTEGER NOT NULL DEFAULT 0;
