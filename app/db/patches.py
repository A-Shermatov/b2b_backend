def _create_enum_sql(type_name: str, values: tuple[str, ...]) -> str:
    values_sql = ", ".join(f"'{value}'" for value in values)
    return f"""
    DO $$
    BEGIN
        CREATE TYPE {type_name} AS ENUM ({values_sql});
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END
    $$;
    """


def _migrate_report_reason_sql(type_name: str, table_name: str) -> str:
    new_values = ("spam", "fraud", "counterfeit", "abuse", "other")
    values_sql = ", ".join(f"'{value}'" for value in new_values)
    new_type = f"{type_name}_new"
    return f"""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_type WHERE typname = '{type_name}'
        ) AND EXISTS (
            SELECT 1
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = '{type_name}'
              AND pg_enum.enumlabel = 'misleading'
        ) THEN
            CREATE TYPE {new_type} AS ENUM ({values_sql});
            ALTER TABLE {table_name}
                ALTER COLUMN reason TYPE {new_type}
                USING (
                    CASE reason::text
                        WHEN 'spam' THEN 'spam'
                        WHEN 'prohibited' THEN 'fraud'
                        WHEN 'copyright' THEN 'counterfeit'
                        WHEN 'misleading' THEN 'abuse'
                        WHEN 'other' THEN 'other'
                        WHEN 'fraud' THEN 'fraud'
                        WHEN 'counterfeit' THEN 'counterfeit'
                        WHEN 'abuse' THEN 'abuse'
                        ELSE 'other'
                    END::{new_type}
                );
            DROP TYPE {type_name};
            ALTER TYPE {new_type} RENAME TO {type_name};
        ELSIF NOT EXISTS (
            SELECT 1 FROM pg_type WHERE typname = '{type_name}'
        ) THEN
            CREATE TYPE {type_name} AS ENUM ({values_sql});
        END IF;
    END
    $$;
    """


COMPAT_PATCHES = [
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'rfq_status'
              AND pg_enum.enumlabel = 'archived'
        ) THEN
            ALTER TYPE rfq_status ADD VALUE 'archived';
        END IF;
    END
    $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'proposal_status'
              AND pg_enum.enumlabel = 'archived'
        ) THEN
            ALTER TYPE proposal_status ADD VALUE 'archived';
        END IF;
    END
    $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'proposal_status'
              AND pg_enum.enumlabel = 'pending_supplier'
        ) THEN
            ALTER TYPE proposal_status ADD VALUE 'pending_supplier';
        END IF;
    END
    $$;
    """,
    _create_enum_sql("rfq_report_reason", ("spam", "fraud", "counterfeit", "abuse", "other")),
    _create_enum_sql("proposal_report_reason", ("spam", "fraud", "counterfeit", "abuse", "other")),
    _create_enum_sql("rfq_report_status", ("open", "resolved", "dismissed")),
    _create_enum_sql("proposal_report_status", ("open", "resolved", "dismissed")),
    """
    CREATE TABLE IF NOT EXISTS rfq_reports (
        id SERIAL PRIMARY KEY,
        rfq_id VARCHAR(36) NOT NULL REFERENCES rfqs(id),
        reporter_user_id INTEGER NOT NULL REFERENCES users(id),
        reason rfq_report_reason NOT NULL,
        details TEXT,
        status rfq_report_status NOT NULL DEFAULT 'open',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at TIMESTAMPTZ,
        resolved_by_id INTEGER REFERENCES users(id)
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_rfq_open_report
    ON rfq_reports (rfq_id, reporter_user_id)
    WHERE status = 'open';
    """,
    """
    CREATE TABLE IF NOT EXISTS proposal_reports (
        id SERIAL PRIMARY KEY,
        proposal_id INTEGER NOT NULL REFERENCES proposals(id),
        reporter_user_id INTEGER NOT NULL REFERENCES users(id),
        reason proposal_report_reason NOT NULL,
        details TEXT,
        status proposal_report_status NOT NULL DEFAULT 'open',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at TIMESTAMPTZ,
        resolved_by_id INTEGER REFERENCES users(id)
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_proposal_open_report
    ON proposal_reports (proposal_id, reporter_user_id)
    WHERE status = 'open';
    """,
    _create_enum_sql("dispute_status", ("open", "under_review", "resolved", "appealed")),
    _create_enum_sql(
        "dispute_resolution",
        ("release_funds", "refund_buyer", "partial_refund", "close_case"),
    ),
    """
    CREATE TABLE IF NOT EXISTS disputes (
        id SERIAL PRIMARY KEY,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        status dispute_status NOT NULL DEFAULT 'open',
        opened_by_actor_id INTEGER REFERENCES actors(id),
        buyer_statement TEXT,
        supplier_statement TEXT,
        admin_instructions TEXT,
        resolution dispute_resolution,
        resolution_note TEXT,
        partial_buyer_amount DOUBLE PRECISION,
        resolved_at TIMESTAMPTZ,
        resolved_by_id INTEGER REFERENCES users(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "ALTER TABLE disputes ADD COLUMN IF NOT EXISTS admin_instructions TEXT;",
    "CREATE INDEX IF NOT EXISTS ix_disputes_contract_id ON disputes (contract_id);",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dispute_active_contract
    ON disputes (contract_id)
    WHERE status IN ('open', 'under_review', 'appealed');
    """,
    """
    CREATE TABLE IF NOT EXISTS dispute_evidence (
        id SERIAL PRIMARY KEY,
        dispute_id INTEGER NOT NULL REFERENCES disputes(id),
        uploaded_by_actor_id INTEGER REFERENCES actors(id),
        file_name VARCHAR(255) NOT NULL,
        file_url VARCHAR(500) NOT NULL,
        file_type VARCHAR(100) NOT NULL,
        note TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_dispute_evidence_dispute_id ON dispute_evidence (dispute_id);",
    _create_enum_sql(
        "platform_payment_type",
        ("platform_revenue", "subscription", "commission", "refund", "payout"),
    ),
    _create_enum_sql(
        "platform_payment_status",
        ("pending", "processing", "paid", "failed", "refunded", "cancelled"),
    ),
    _create_enum_sql("platform_payment_gateway", ("manual", "mock", "stripe", "yookassa")),
    """
    CREATE TABLE IF NOT EXISTS platform_payments (
        id SERIAL PRIMARY KEY,
        external_id VARCHAR(100),
        type platform_payment_type NOT NULL,
        status platform_payment_status NOT NULL DEFAULT 'pending',
        gateway platform_payment_gateway NOT NULL DEFAULT 'manual',
        amount DOUBLE PRECISION NOT NULL,
        commission DOUBLE PRECISION NOT NULL DEFAULT 0,
        currency VARCHAR(10) NOT NULL DEFAULT 'TJS',
        title VARCHAR(255) NOT NULL,
        description TEXT,
        actor_id INTEGER REFERENCES actors(id),
        invoice_id INTEGER REFERENCES supplier_invoices(id),
        withdrawal_id INTEGER REFERENCES withdrawals(id),
        contract_id INTEGER REFERENCES contracts(id),
        subscription_user_id INTEGER REFERENCES users(id),
        metadata JSONB,
        paid_at TIMESTAMPTZ,
        failed_at TIMESTAMPTZ,
        refunded_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_external_id ON platform_payments (external_id);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_actor_id ON platform_payments (actor_id);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_invoice_id ON platform_payments (invoice_id);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_withdrawal_id ON platform_payments (withdrawal_id);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_contract_id ON platform_payments (contract_id);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_subscription_user_id ON platform_payments (subscription_user_id);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_type ON platform_payments (type);",
    "CREATE INDEX IF NOT EXISTS ix_platform_payments_status ON platform_payments (status);",
    _migrate_report_reason_sql("catalog_item_report_reason", "catalog_item_reports"),
    _migrate_report_reason_sql("rfq_report_reason", "rfq_reports"),
    _migrate_report_reason_sql("proposal_report_reason", "proposal_reports"),
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_id INTEGER;",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS company_id INTEGER;",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_agent VARCHAR(500);",
    "ALTER TABLE reviews ALTER COLUMN created_at SET DEFAULT NOW();",
    """
    CREATE TABLE IF NOT EXISTS platform_settings (
        key VARCHAR(100) PRIMARY KEY,
        value JSONB,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    INSERT INTO platform_settings (key, value) VALUES
        ('commission_percent', '5'::jsonb),
        ('commission_min', '0'::jsonb),
        ('max_contract_amount', 'null'::jsonb)
    ON CONFLICT (key) DO NOTHING;
    """,
    """
    INSERT INTO permissions (name, resource, action)
    SELECT v.name, v.resource, v.action
    FROM (VALUES
        ('platform_settings.view', 'platform_settings', 'view'),
        ('platform_settings.update', 'platform_settings', 'update')
    ) AS v(name, resource, action)
    WHERE NOT EXISTS (SELECT 1 FROM permissions p WHERE p.name = v.name);
    """,
    """
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r
    JOIN permissions p ON (
        (r.name IN ('admin', 'superadmin') AND p.name IN ('platform_settings.view', 'platform_settings.update'))
        OR (r.name = 'moderator' AND p.name = 'platform_settings.view')
    )
    WHERE NOT EXISTS (
        SELECT 1 FROM role_permissions rp
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
    );
    """,
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS commission_amount DOUBLE PRECISION NOT NULL DEFAULT 0;",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS commission_percent DOUBLE PRECISION;",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS commission_min DOUBLE PRECISION;",
    "ALTER TABLE payment_milestones ADD COLUMN IF NOT EXISTS commission_amount DOUBLE PRECISION NOT NULL DEFAULT 0;",
    """
    CREATE TABLE IF NOT EXISTS payment_gateway_transactions (
        id SERIAL PRIMARY KEY,
        order_id VARCHAR(64) NOT NULL UNIQUE,
        gateway VARCHAR(20) NOT NULL,
        milestone_id INTEGER NOT NULL REFERENCES payment_milestones(id),
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        buyer_actor_id INTEGER NOT NULL REFERENCES actors(id),
        amount NUMERIC(14, 2) NOT NULL,
        currency VARCHAR(10) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        external_transaction_id VARCHAR(64),
        payment_url TEXT,
        transaction_type VARCHAR(50),
        account_masked VARCHAR(64),
        raw_callback JSONB,
        paid_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_payment_gateway_transactions_milestone_id ON payment_gateway_transactions (milestone_id);",
    "CREATE INDEX IF NOT EXISTS ix_payment_gateway_transactions_external_id ON payment_gateway_transactions (external_transaction_id);",
    "ALTER TABLE payment_gateway_transactions ADD COLUMN IF NOT EXISTS marketplace_meta JSONB;",
    """
    ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS catalog_item_id INTEGER REFERENCES catalog_items(id);
    """,
    "CREATE INDEX IF NOT EXISTS ix_rfqs_catalog_item_id ON rfqs (catalog_item_id);",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'notification_type'
              AND pg_enum.enumlabel = 'support'
        ) THEN
            ALTER TYPE notification_type ADD VALUE 'support';
        END IF;
    END
    $$;
    """,
    _create_enum_sql("support_ticket_status", ("open", "closed")),
    """
    CREATE TABLE IF NOT EXISTS support_tickets (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        side actor_side NOT NULL,
        status support_ticket_status NOT NULL DEFAULT 'open',
        assigned_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        last_message_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at TIMESTAMPTZ
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_support_tickets_one_open_per_user
    ON support_tickets (user_id)
    WHERE status = 'open';
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_support_tickets_status_last_message
    ON support_tickets (status, last_message_at DESC NULLS LAST);
    """,
    """
    CREATE TABLE IF NOT EXISTS support_messages (
        id SERIAL PRIMARY KEY,
        ticket_id INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
        sender_id INTEGER NOT NULL REFERENCES users(id),
        text TEXT NOT NULL DEFAULT '',
        status message_delivery_status NOT NULL DEFAULT 'sent',
        delivered_at TIMESTAMPTZ,
        viewed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_support_messages_ticket_id
    ON support_messages (ticket_id, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS support_message_attachments (
        id SERIAL PRIMARY KEY,
        message_id INTEGER NOT NULL UNIQUE REFERENCES support_messages(id) ON DELETE CASCADE,
        file_name VARCHAR(255) NOT NULL,
        file_url VARCHAR(500) NOT NULL,
        file_type VARCHAR(100) NOT NULL
    );
    """,
    """
    INSERT INTO permissions (name, resource, action)
    SELECT v.name, v.resource, v.action
    FROM (VALUES
        ('admin_support.view', 'admin_support', 'view'),
        ('admin_support.update', 'admin_support', 'update')
    ) AS v(name, resource, action)
    WHERE NOT EXISTS (SELECT 1 FROM permissions p WHERE p.name = v.name);
    """,
    """
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r
    JOIN permissions p ON (
        (r.name IN ('admin', 'superadmin') AND p.name IN ('admin_support.view', 'admin_support.update'))
        OR (r.name = 'moderator' AND p.name IN ('admin_support.view', 'admin_support.update'))
    )
    WHERE NOT EXISTS (
        SELECT 1 FROM role_permissions rp
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
    );
    """,
    "ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS invoice BOOLEAN NOT NULL DEFAULT TRUE;",
    _create_enum_sql(
        "purchase_approval_status",
        ("wait", "auto", "approved", "rejected"),
    ),
    """
    CREATE TABLE IF NOT EXISTS purchase_approvals (
        id SERIAL PRIMARY KEY,
        company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        requester_user_id INTEGER NOT NULL REFERENCES users(id),
        title VARCHAR(500) NOT NULL,
        supplier_name VARCHAR(255) NOT NULL DEFAULT '',
        amount DOUBLE PRECISION NOT NULL,
        currency VARCHAR(10) NOT NULL DEFAULT 'TJS',
        reason TEXT NOT NULL DEFAULT '',
        status purchase_approval_status NOT NULL DEFAULT 'wait',
        limit_label VARCHAR(100) NOT NULL DEFAULT '—',
        requester_role VARCHAR(64) NOT NULL DEFAULT '',
        rfq_id VARCHAR(64),
        decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        decided_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_purchase_approvals_company_status
    ON purchase_approvals (company_id, status);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_purchase_approvals_requester
    ON purchase_approvals (requester_user_id);
    """,
    """
    ALTER TABLE company_profiles
        ADD COLUMN IF NOT EXISTS warehouse_area VARCHAR(255),
        ADD COLUMN IF NOT EXISTS markets VARCHAR(500),
        ADD COLUMN IF NOT EXISTS brands VARCHAR(500),
        ADD COLUMN IF NOT EXISTS notable_clients TEXT,
        ADD COLUMN IF NOT EXISTS gallery JSONB NOT NULL DEFAULT '[]'::jsonb;
    """,
    """
    ALTER TABLE purchase_approvals
        ADD COLUMN IF NOT EXISTS cart_items JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS rfq_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64);
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_approvals_idempotency
    ON purchase_approvals (requester_user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
    """,
    """
    ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until TIMESTAMPTZ;
    """,
    """
    ALTER TABLE users ADD COLUMN IF NOT EXISTS block_reason TEXT;
    """,
    """
    ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
    """,
    """
    ALTER TABLE companies ADD COLUMN IF NOT EXISTS vip BOOLEAN NOT NULL DEFAULT FALSE;
    """,
    """
    ALTER TABLE companies ADD COLUMN IF NOT EXISTS vip_until TIMESTAMPTZ;
    """,
    """
    ALTER TABLE reviews ADD COLUMN IF NOT EXISTS service_rating INTEGER;
    """,
    """
    ALTER TABLE reviews ADD COLUMN IF NOT EXISTS delivery_rating INTEGER;
    """,
    """
    ALTER TABLE reviews ADD COLUMN IF NOT EXISTS quality_rating INTEGER;
    """,
    """
    ALTER TABLE reviews ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';
    """,
    # A member's role can differ between purchases (buyer) and sales (supplier),
    # or be "none" to close that side. Without a row the company role applies.
    """
    CREATE TABLE IF NOT EXISTS company_user_side_roles (
        company_user_id INTEGER NOT NULL REFERENCES company_users(id) ON DELETE CASCADE,
        side TEXT NOT NULL CHECK (side IN ('buyer', 'supplier')),
        role TEXT NOT NULL CHECK (role IN ('admin', 'moderator', 'accountant', 'viewer', 'none')),
        PRIMARY KEY (company_user_id, side)
    );
    """,
    # "viewer" (look, change nothing) can be given per side as well.
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'company_user_side_roles_role_check'
              AND pg_get_constraintdef(oid) LIKE '%viewer%'
        ) THEN
            ALTER TABLE company_user_side_roles
                DROP CONSTRAINT IF EXISTS company_user_side_roles_role_check;
            ALTER TABLE company_user_side_roles
                ADD CONSTRAINT company_user_side_roles_role_check
                CHECK (role IN ('admin', 'moderator', 'accountant', 'viewer', 'none'));
        END IF;
    END
    $$;
    """,
    # Personal notifications about new requests by category (supplier side).
    """
    CREATE TABLE IF NOT EXISTS rfq_category_subscriptions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        supplier_actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, supplier_actor_id, category_id)
    );
    CREATE INDEX IF NOT EXISTS idx_rfq_category_subscriptions_category
        ON rfq_category_subscriptions (category_id);
    """,
    # Who wrote the review on behalf of the company (shown with role and company).
    "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS author_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;",
    """
    ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_contract_id_key;
    """,
    """
    ALTER TABLE reviews DROP CONSTRAINT IF EXISTS uq_reviews_contract;
    """,
    """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_review_contract'
      ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_reviews_contract_reviewer'
      ) THEN
        ALTER TABLE reviews
          ADD CONSTRAINT uq_reviews_contract_reviewer UNIQUE (contract_id, reviewer_actor_id);
      END IF;
    END $$;
    """,
    """
    INSERT INTO public.role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM public.roles r
    JOIN public.permissions p ON p.name IN ('reviews.view', 'reviews.create')
    WHERE r.name IN ('supplier', 'both')
      AND NOT EXISTS (
          SELECT 1 FROM public.role_permissions rp
          WHERE rp.role_id = r.id AND rp.permission_id = p.id
      );
    """,
    """
    CREATE TABLE IF NOT EXISTS nav_section_seen (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
        section VARCHAR(32) NOT NULL,
        seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, actor_id, section)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS direct_conversations (
        id SERIAL PRIMARY KEY,
        buyer_actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
        supplier_actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (buyer_actor_id, supplier_actor_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_direct_conversations_supplier
    ON direct_conversations (supplier_actor_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS direct_messages (
        id SERIAL PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES direct_conversations(id) ON DELETE CASCADE,
        sender_id INTEGER NOT NULL REFERENCES users(id),
        text TEXT NOT NULL DEFAULT '',
        status message_delivery_status NOT NULL DEFAULT 'sent',
        delivered_at TIMESTAMPTZ,
        viewed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_direct_messages_conversation
    ON direct_messages (conversation_id, created_at, id);
    """,
    """
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
    """,
    """
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
    """,
    "CREATE INDEX IF NOT EXISTS ix_subscription_payment_orders_user_id ON subscription_payment_orders (user_id);",
    "ALTER TABLE subscription_payment_orders ADD COLUMN IF NOT EXISTS marketplace_meta JSONB;",
    """
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
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_company_change_requests_one_pending
    ON company_change_requests (company_id)
    WHERE status = 'pending';
    """,
    "CREATE INDEX IF NOT EXISTS ix_company_change_requests_status ON company_change_requests (status, created_at DESC);",
    # Every change to a public profile waits for an admin: the company's whole
    # profile, and a person's name, photo and personal page (target_user_id).
    "ALTER TABLE company_change_requests ALTER COLUMN company_id DROP NOT NULL;",
    "ALTER TABLE company_change_requests ADD COLUMN IF NOT EXISTS target_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_change_requests_one_pending_user
    ON company_change_requests (target_user_id)
    WHERE status = 'pending' AND target_user_id IS NOT NULL;
    """,
    """
    CREATE TABLE IF NOT EXISTS review_media (
        id SERIAL PRIMARY KEY,
        review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
        file_name VARCHAR(255) NOT NULL,
        file_url VARCHAR(500) NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_review_media_review_id ON review_media (review_id);",
    """
    CREATE TABLE IF NOT EXISTS review_helpful_votes (
        review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
        voter_actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (review_id, voter_actor_id)
    );
    """,
    "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS helpful_count INTEGER NOT NULL DEFAULT 0;",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS deleted_by_user_id INTEGER REFERENCES users(id);",
    "ALTER TABLE proposal_messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;",
    "ALTER TABLE proposal_messages ADD COLUMN IF NOT EXISTS deleted_by_user_id INTEGER REFERENCES users(id);",
    """
    CREATE TABLE IF NOT EXISTS payment_gateway_http_logs (
        id SERIAL PRIMARY KEY,
        gateway VARCHAR(20) NOT NULL DEFAULT 'alif',
        direction VARCHAR(20) NOT NULL,
        operation VARCHAR(64) NOT NULL,
        order_id VARCHAR(64),
        method VARCHAR(10),
        url TEXT,
        request_headers JSONB,
        request_body JSONB,
        response_status INTEGER,
        response_body JSONB,
        response_raw TEXT,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        responded_at TIMESTAMPTZ
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_payment_gateway_http_logs_order_id ON payment_gateway_http_logs (order_id);",
    "CREATE INDEX IF NOT EXISTS ix_payment_gateway_http_logs_created_at ON payment_gateway_http_logs (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS ix_payment_gateway_http_logs_operation ON payment_gateway_http_logs (gateway, operation, created_at DESC);",
    "ALTER TABLE dispute_evidence ALTER COLUMN uploaded_by_actor_id DROP NOT NULL;",
    """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash VARCHAR(128) NOT NULL UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ip_address VARCHAR(64)
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user ON password_reset_tokens (user_id, created_at DESC);",
    "ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;",
    "UPDATE catalog_items SET updated_at = created_at WHERE updated_at IS NULL;",
    "ALTER TABLE catalog_items ALTER COLUMN updated_at SET DEFAULT NOW();",
    """
    CREATE OR REPLACE FUNCTION catalog_items_touch_updated_at() RETURNS trigger AS $$
    BEGIN
        NEW.updated_at := NOW();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    """
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
    """,
    "CREATE INDEX IF NOT EXISTS ix_catalog_items_status_created ON catalog_items (status, created_at DESC);",
    """
    ALTER TABLE users
        ADD COLUMN IF NOT EXISTS individual_verification_status VARCHAR(20) NOT NULL DEFAULT 'none',
        ADD COLUMN IF NOT EXISTS individual_verification_reason TEXT,
        ADD COLUMN IF NOT EXISTS individual_verification_submitted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS individual_verified_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS individual_verified_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
    """,
    """
    DO $$
    BEGIN
        ALTER TABLE users ADD CONSTRAINT users_individual_verification_status_check
            CHECK (individual_verification_status IN ('none', 'pending', 'needs_documents', 'verified', 'rejected'));
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END
    $$;
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_users_individual_verification_status
        ON users (individual_verification_status)
        WHERE individual_verification_status <> 'none';
    """,
    """
    CREATE TABLE IF NOT EXISTS user_documents (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        doc_type VARCHAR(20) NOT NULL DEFAULT 'other',
        file_name VARCHAR(255),
        file_url TEXT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'uploaded',
        uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_user_documents_user ON user_documents (user_id, uploaded_at DESC);",
    "ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS bank VARCHAR(20);",
    "UPDATE withdrawals SET bank = 'alif' WHERE bank IS NULL;",
    "CREATE INDEX IF NOT EXISTS ix_withdrawals_bank ON withdrawals (bank);",
    # Why a moderator took an RFQ off the board; shown to the buyer in the archive.
    "ALTER TABLE rfqs ADD COLUMN IF NOT EXISTS moderation_reason TEXT;",
    "ALTER TABLE rfqs ADD COLUMN IF NOT EXISTS moderated_at TIMESTAMPTZ;",
    # Reliability mark set by an admin; NULL means "computed from deals".
    "ALTER TABLE actors ADD COLUMN IF NOT EXISTS reputation_override VARCHAR(16);",
    "ALTER TABLE actors ADD COLUMN IF NOT EXISTS reputation_override_reason TEXT;",
    "ALTER TABLE actors ADD COLUMN IF NOT EXISTS reputation_override_by INTEGER REFERENCES users(id) ON DELETE SET NULL;",
    "ALTER TABLE actors ADD COLUMN IF NOT EXISTS reputation_override_at TIMESTAMPTZ;",
    # How the buyer pays: "escrow" keeps the money in the bank until acceptance,
    # "direct" sends it to the supplier as soon as it is paid.
    "ALTER TABLE rfqs ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(10) NOT NULL DEFAULT 'escrow';",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(10) NOT NULL DEFAULT 'escrow';",
    """
    DO $$
    BEGIN
        ALTER TABLE rfqs ADD CONSTRAINT rfqs_payment_mode_check
            CHECK (payment_mode IN ('escrow', 'direct'));
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END
    $$;
    """,
    """
    DO $$
    BEGIN
        ALTER TABLE contracts ADD CONSTRAINT contracts_payment_mode_check
            CHECK (payment_mode IN ('escrow', 'direct'));
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END
    $$;
    """,
    # A buyer's review of a deal that came from the catalog also rates that item.
    "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS catalog_item_id INTEGER REFERENCES catalog_items(id) ON DELETE SET NULL;",
    "CREATE INDEX IF NOT EXISTS ix_reviews_catalog_item ON reviews (catalog_item_id, created_at DESC) WHERE catalog_item_id IS NOT NULL;",
    """
    UPDATE reviews r
    SET catalog_item_id = rf.catalog_item_id
    FROM contracts c
    JOIN rfqs rf ON rf.id = c.rfq_id
    WHERE c.id = r.contract_id
      AND r.reviewer_actor_id = c.buyer_actor_id
      AND r.catalog_item_id IS NULL
      AND rf.catalog_item_id IS NOT NULL;
    """,
    # The buyer puts a finished request away; it keeps its own status.
    "ALTER TABLE rfqs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;",
    # Why the buyer cancelled the request; suppliers who answered see it.
    "ALTER TABLE rfqs ADD COLUMN IF NOT EXISTS cancel_reason TEXT;",
    "CREATE INDEX IF NOT EXISTS ix_rfqs_actor_archived ON rfqs (actor_id, archived_at);",
    # The person's photo, shown in the menu and next to their messages.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;",
    # The platform bot that posts new requests to a Telegram channel (one row).
    """
    CREATE TABLE IF NOT EXISTS telegram_settings (
        id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        bot_token_enc TEXT,
        bot_username VARCHAR(64),
        channel_id VARCHAR(128),
        channel_title VARCHAR(255),
        site_url VARCHAR(255),
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_posts (
        id SERIAL PRIMARY KEY,
        rfq_id VARCHAR(36) NOT NULL UNIQUE REFERENCES rfqs(id) ON DELETE CASCADE,
        chat_id VARCHAR(128),
        message_id BIGINT,
        status VARCHAR(16) NOT NULL,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_telegram_posts_created ON telegram_posts (created_at DESC);",
    # The HTTP audit used to store the Telegram bot token from PUT /admin/telegram in
    # plain text; hide it in rows written before the redaction covered *_token.
    """
    UPDATE audit_logs
    SET details = jsonb_set(details, '{request_body,bot_token}', '"***"'::jsonb)
    WHERE details #>> '{request_body,bot_token}' IS NOT NULL
      AND details #>> '{request_body,bot_token}' <> '***';
    """,
]
