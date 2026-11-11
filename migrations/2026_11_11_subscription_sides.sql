-- A paid plan belongs to one cabinet: the buying side and the selling side
-- are bought separately. Plans bought before this split stay with the selling
-- side, where they were sold.
ALTER TABLE supplier_subscriptions ADD COLUMN IF NOT EXISTS side VARCHAR(16) NOT NULL DEFAULT 'supplier';
DROP INDEX IF EXISTS ix_supplier_subscriptions_user_id;
CREATE UNIQUE INDEX IF NOT EXISTS ux_supplier_subscriptions_user_side
ON supplier_subscriptions (user_id, side);
ALTER TABLE subscription_payment_orders ADD COLUMN IF NOT EXISTS side VARCHAR(16) NOT NULL DEFAULT 'supplier';

-- The buying cabinet has a tariff page of its own now.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.name IN ('subscription.view', 'subscription.update')
WHERE r.name = 'buyer'
  AND NOT EXISTS (
      SELECT 1 FROM role_permissions rp
      WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );
