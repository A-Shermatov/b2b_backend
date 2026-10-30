-- Marketplace split metadata for Alif subscription checkouts (platform terminal gets 100%).
ALTER TABLE subscription_payment_orders ADD COLUMN IF NOT EXISTS marketplace_meta JSONB;
