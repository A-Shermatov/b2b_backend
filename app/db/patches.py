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
        uploaded_by_actor_id INTEGER NOT NULL REFERENCES actors(id),
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
    "ALTER TABLE reviews ALTER COLUMN created_at SET DEFAULT NOW();",
]
