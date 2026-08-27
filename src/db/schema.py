import logging

from sqlalchemy import inspect, select, text

from src.db.base import Base
from src.db.session import AsyncSessionLocal, engine
from src.models import Actor, Company, User
from src.modules.actors.service import ActorService

logger = logging.getLogger(__name__)

FK_MIGRATIONS: list[tuple[str, str, str | None]] = [
    ("rfqs", "actor_id", "buyer"),
    ("proposals", "supplier_actor_id", "supplier"),
    ("contracts", "buyer_actor_id", "buyer"),
    ("contracts", "supplier_actor_id", "supplier"),
    ("reviews", "reviewer_actor_id", None),
    ("reviews", "target_actor_id", None),
]


def _build_company_actor_map(sync_conn) -> dict[tuple[int, str], int]:
    rows = sync_conn.execute(
        text(
            "SELECT id, company_id, side::text AS side "
            "FROM actors WHERE kind = 'company' AND company_id IS NOT NULL"
        )
    ).fetchall()
    return {(row.company_id, row.side): row.id for row in rows}


def _fk_targets_companies(inspector, table: str, column: str) -> str | None:
    for fk in inspector.get_foreign_keys(table):
        if column in fk.get("constrained_columns", []):
            if fk.get("referred_table") == "companies":
                return fk["name"]
    return None


def _remap_column_values(
    sync_conn,
    table: str,
    column: str,
    company_actor_map: dict[tuple[int, str], int],
    side: str | None,
) -> None:
    rows = sync_conn.execute(text(f"SELECT id, {column} AS val FROM {table}")).fetchall()
    for row in rows:
        old_id = row.val
        if old_id is None:
            continue
        if side:
            new_id = company_actor_map.get((old_id, side))
        else:
            new_id = company_actor_map.get((old_id, "buyer")) or company_actor_map.get(
                (old_id, "supplier")
            )
        if new_id is None:
            continue
        sync_conn.execute(
            text(f"UPDATE {table} SET {column} = :new_id WHERE id = :row_id"),
            {"new_id": new_id, "row_id": row.id},
        )


def _switch_fk_to_actors(sync_conn, inspector, table: str, column: str) -> None:
    fk_name = _fk_targets_companies(inspector, table, column)
    if not fk_name:
        return
    sync_conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{fk_name}"'))
    new_fk = f"{table}_{column}_actors_fkey"
    sync_conn.execute(
        text(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{new_fk}" '
            f'FOREIGN KEY ("{column}") REFERENCES actors (id)'
        )
    )


def _migrate_legacy_actor_fks_sync(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = inspector.get_table_names()
    if "actors" not in tables:
        return

    company_actor_map = _build_company_actor_map(sync_conn)
    if not company_actor_map:
        return

    needs_migration = False
    for table, column, _side in FK_MIGRATIONS:
        if table not in tables:
            continue
        if _fk_targets_companies(inspector, table, column):
            needs_migration = True
            break

    if "rfq_invited_suppliers" in tables:
        cols = {c["name"] for c in inspector.get_columns("rfq_invited_suppliers")}
        if "supplier_id" in cols:
            needs_migration = True

    if not needs_migration:
        return

    logger.info("Migrating legacy company FK columns to actors")

    for table, column, side in FK_MIGRATIONS:
        if table not in tables:
            continue
        if not _fk_targets_companies(inspector, table, column):
            continue
        _remap_column_values(sync_conn, table, column, company_actor_map, side)
        _switch_fk_to_actors(sync_conn, inspector, table, column)

    if "rfq_invited_suppliers" in tables:
        cols = {c["name"] for c in inspector.get_columns("rfq_invited_suppliers")}
        if "supplier_id" in cols and "supplier_actor_id" not in cols:
            _remap_column_values(
                sync_conn,
                "rfq_invited_suppliers",
                "supplier_id",
                company_actor_map,
                "supplier",
            )
            sync_conn.execute(
                text(
                    'ALTER TABLE rfq_invited_suppliers '
                    "RENAME COLUMN supplier_id TO supplier_actor_id"
                )
            )
            inspector = inspect(sync_conn)
            _switch_fk_to_actors(sync_conn, inspector, "rfq_invited_suppliers", "supplier_actor_id")


async def ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_admin_rfq_proposal_schema_sync)
        await conn.run_sync(_ensure_admin_disputes_schema_sync)
        await conn.run_sync(_ensure_admin_finance_schema_sync)
        await conn.run_sync(_ensure_admin_reports_schema_sync)
    logger.info("Database schema ensured via metadata.create_all")


def _migrate_report_reason_enum(sync_conn, type_name: str, table_name: str) -> None:
    new_values = ("spam", "fraud", "counterfeit", "abuse", "other")
    values_sql = ", ".join(f"'{value}'" for value in new_values)
    new_type = f"{type_name}_new"
    sync_conn.execute(
        text(
            f"""
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
        )
    )


def _ensure_admin_reports_schema_sync(sync_conn) -> None:
    for table_name, type_name in (
        ("catalog_item_reports", "catalog_item_report_reason"),
        ("rfq_reports", "rfq_report_reason"),
        ("proposal_reports", "proposal_report_reason"),
    ):
        _migrate_report_reason_enum(sync_conn, type_name, table_name)


def _ensure_admin_rfq_proposal_schema_sync(sync_conn) -> None:
    sync_conn.execute(
        text(
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
            """
        )
    )

    for type_name, values in (
        (
            "rfq_report_reason",
            ("spam", "fraud", "counterfeit", "abuse", "other"),
        ),
        (
            "proposal_report_reason",
            ("spam", "fraud", "counterfeit", "abuse", "other"),
        ),
        ("rfq_report_status", ("open", "resolved", "dismissed")),
        ("proposal_report_status", ("open", "resolved", "dismissed")),
    ):
        values_sql = ", ".join(f"'{value}'" for value in values)
        sync_conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    CREATE TYPE {type_name} AS ENUM ({values_sql});
                EXCEPTION
                    WHEN duplicate_object THEN NULL;
                END
                $$;
                """
            )
        )

    sync_conn.execute(
        text(
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
            """
        )
    )
    sync_conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rfq_open_report
            ON rfq_reports (rfq_id, reporter_user_id)
            WHERE status = 'open';
            """
        )
    )
    sync_conn.execute(
        text(
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
            """
        )
    )
    sync_conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_proposal_open_report
            ON proposal_reports (proposal_id, reporter_user_id)
            WHERE status = 'open';
            """
        )
    )


def _ensure_admin_disputes_schema_sync(sync_conn) -> None:
    for type_name, values in (
        (
            "dispute_status",
            ("open", "under_review", "resolved", "appealed"),
        ),
        (
            "dispute_resolution",
            ("release_funds", "refund_buyer", "partial_refund", "close_case"),
        ),
    ):
        values_sql = ", ".join(f"'{value}'" for value in values)
        sync_conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    CREATE TYPE {type_name} AS ENUM ({values_sql});
                EXCEPTION
                    WHEN duplicate_object THEN NULL;
                END
                $$;
                """
            )
        )

    sync_conn.execute(
        text(
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
            """
        )
    )
    sync_conn.execute(
        text(
            """
            ALTER TABLE disputes
            ADD COLUMN IF NOT EXISTS admin_instructions TEXT;
            """
        )
    )
    sync_conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_disputes_contract_id ON disputes (contract_id);
            """
        )
    )
    sync_conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_dispute_active_contract
            ON disputes (contract_id)
            WHERE status IN ('open', 'under_review', 'appealed');
            """
        )
    )
    sync_conn.execute(
        text(
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
            """
        )
    )
    sync_conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_dispute_evidence_dispute_id
            ON dispute_evidence (dispute_id);
            """
        )
    )


def _ensure_admin_finance_schema_sync(sync_conn) -> None:
    for type_name, values in (
        (
            "platform_payment_type",
            (
                "platform_revenue",
                "subscription",
                "commission",
                "refund",
                "payout",
            ),
        ),
        (
            "platform_payment_status",
            (
                "pending",
                "processing",
                "paid",
                "failed",
                "refunded",
                "cancelled",
            ),
        ),
        (
            "platform_payment_gateway",
            ("manual", "mock", "stripe", "yookassa"),
        ),
    ):
        values_sql = ", ".join(f"'{value}'" for value in values)
        sync_conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    CREATE TYPE {type_name} AS ENUM ({values_sql});
                EXCEPTION
                    WHEN duplicate_object THEN NULL;
                END
                $$;
                """
            )
        )

    sync_conn.execute(
        text(
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
            """
        )
    )
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_external_id ON platform_payments (external_id);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_actor_id ON platform_payments (actor_id);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_invoice_id ON platform_payments (invoice_id);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_withdrawal_id ON platform_payments (withdrawal_id);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_contract_id ON platform_payments (contract_id);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_subscription_user_id ON platform_payments (subscription_user_id);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_type ON platform_payments (type);",
        "CREATE INDEX IF NOT EXISTS ix_platform_payments_status ON platform_payments (status);",
    ):
        sync_conn.execute(text(index_sql))


async def migrate_legacy_actor_fks() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_migrate_legacy_actor_fks_sync)


async def backfill_actors_if_needed() -> None:
    async with AsyncSessionLocal() as db:
        actor_count = await db.execute(select(Actor.id).limit(1))
        actor_svc = ActorService(db)

        if actor_count.scalar_one_or_none() is not None:
            users = (await db.execute(select(User))).scalars().all()
            for user in users:
                await actor_svc.ensure_individual_actors_for_user(user)
            await db.commit()
            return

        companies = (await db.execute(select(Company))).scalars().all()
        if not companies:
            users = (await db.execute(select(User))).scalars().all()
            for user in users:
                await actor_svc.ensure_individual_actors_for_user(user)
            await db.commit()
            return

        for company in companies:
            await actor_svc.ensure_company_actor(company, company.actor_type)
        users = (await db.execute(select(User))).scalars().all()
        for user in users:
            await actor_svc.ensure_individual_actors_for_user(user)
        await db.commit()
        logger.info("Backfilled actors from existing companies")


async def prepare_database() -> None:
    await ensure_schema()
    await backfill_actors_if_needed()
    await migrate_legacy_actor_fks()
