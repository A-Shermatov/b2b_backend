import logging

from app.database import pool
from app.db.patches import COMPAT_PATCHES
from app.db.sql import MIGRATIONS_DIR, execute_sql_file
from app.modules.actors.services import ActorService

logger = logging.getLogger(__name__)

FK_MIGRATIONS: list[tuple[str, str, str | None]] = [
    ("rfqs", "actor_id", "buyer"),
    ("proposals", "supplier_actor_id", "supplier"),
    ("contracts", "buyer_actor_id", "buyer"),
    ("contracts", "supplier_actor_id", "supplier"),
    ("reviews", "reviewer_actor_id", None),
    ("reviews", "target_actor_id", None),
]


async def ensure_schema() -> None:
    if not pool.ready:
        await pool.init()
    async with pool.connection() as conn:
        has_users = await conn.fetchval("SELECT to_regclass('public.users')")
        if has_users is None:
            dump_path = MIGRATIONS_DIR / "dump.sql"
            logger.info("Applying schema dump %s", dump_path)
            await execute_sql_file(conn, dump_path)
            await conn.execute("SET search_path TO public")
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name == "dump.sql":
                continue
            await execute_sql_file(conn, path)
        for patch in COMPAT_PATCHES:
            await conn.execute(patch)
    logger.info("Database schema ready")


async def backfill_actors_if_needed() -> None:
    if not pool.ready:
        await pool.init()

    svc = ActorService()
    async with pool.connection() as conn:
        actor_exists = await conn.fetchval("SELECT id FROM actors LIMIT 1")
        users = [
            dict(row)
            for row in await conn.fetch(
                """
                SELECT id, email, first_name, last_name, role::text AS role
                FROM users
                """
            )
        ]
        companies = [
            dict(row)
            for row in await conn.fetch(
                """
                SELECT
                    id,
                    title,
                    actor_type::text AS actor_type,
                    verification_status::text AS verification_status
                FROM companies
                """
            )
        ]

    if actor_exists is not None:
        for user in users:
            await svc.ensure_individual_actors_for_user(user)
        return

    if not companies:
        for user in users:
            await svc.ensure_individual_actors_for_user(user)
        return

    for company in companies:
        await svc.ensure_company_actor(company, company["actor_type"])
    for user in users:
        await svc.ensure_individual_actors_for_user(user)
    logger.info("Backfilled actors from existing companies")


async def _table_exists(conn, table: str) -> bool:
    return (
        await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = $1
            """,
            table,
        )
        is not None
    )


async def _column_exists(conn, table: str, column: str) -> bool:
    return (
        await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
            """,
            table,
            column,
        )
        is not None
    )


async def _fk_targets_companies(conn, table: str, column: str) -> str | None:
    return await conn.fetchval(
        """
        SELECT tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = $1
          AND kcu.column_name = $2
          AND ccu.table_name = 'companies'
        """,
        table,
        column,
    )


async def _company_actor_map(conn) -> dict[tuple[int, str], int]:
    rows = await conn.fetch(
        """
        SELECT id, company_id, side::text AS side
        FROM actors
        WHERE kind = 'company' AND company_id IS NOT NULL
        """
    )
    return {(row["company_id"], row["side"]): row["id"] for row in rows}


async def _remap_column_values(
    conn,
    table: str,
    column: str,
    company_actor_map: dict[tuple[int, str], int],
    side: str | None,
) -> None:
    rows = await conn.fetch(f"SELECT id, {column} AS val FROM {table}")
    for row in rows:
        old_id = row["val"]
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
        await conn.execute(
            f"UPDATE {table} SET {column} = $1 WHERE id = $2",
            new_id,
            row["id"],
        )


async def _switch_fk_to_actors(conn, table: str, column: str, fk_name: str) -> None:
    await conn.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT "{fk_name}"')
    new_fk = f"{table}_{column}_actors_fkey"
    await conn.execute(
        f'ALTER TABLE "{table}" ADD CONSTRAINT "{new_fk}" '
        f'FOREIGN KEY ("{column}") REFERENCES actors (id)'
    )


async def migrate_legacy_actor_fks() -> None:
    if not pool.ready:
        await pool.init()
    async with pool.connection() as conn:
        if not await _table_exists(conn, "actors"):
            return
        company_actor_map = await _company_actor_map(conn)
        if not company_actor_map:
            return

        needs_migration = False
        for table, column, _side in FK_MIGRATIONS:
            if not await _table_exists(conn, table):
                continue
            if await _fk_targets_companies(conn, table, column):
                needs_migration = True
                break
        if await _table_exists(conn, "rfq_invited_suppliers") and await _column_exists(
            conn, "rfq_invited_suppliers", "supplier_id"
        ):
            needs_migration = True
        if not needs_migration:
            return

        logger.info("Migrating legacy company FK columns to actors")
        for table, column, side in FK_MIGRATIONS:
            if not await _table_exists(conn, table):
                continue
            fk_name = await _fk_targets_companies(conn, table, column)
            if not fk_name:
                continue
            await _remap_column_values(conn, table, column, company_actor_map, side)
            await _switch_fk_to_actors(conn, table, column, fk_name)

        if await _table_exists(conn, "rfq_invited_suppliers") and await _column_exists(
            conn, "rfq_invited_suppliers", "supplier_id"
        ):
            if not await _column_exists(conn, "rfq_invited_suppliers", "supplier_actor_id"):
                await _remap_column_values(
                    conn,
                    "rfq_invited_suppliers",
                    "supplier_id",
                    company_actor_map,
                    "supplier",
                )
                await conn.execute(
                    "ALTER TABLE rfq_invited_suppliers "
                    "RENAME COLUMN supplier_id TO supplier_actor_id"
                )
            fk_name = await _fk_targets_companies(
                conn, "rfq_invited_suppliers", "supplier_actor_id"
            )
            if fk_name:
                await _switch_fk_to_actors(
                    conn, "rfq_invited_suppliers", "supplier_actor_id", fk_name
                )


async def prepare_database() -> None:
    await ensure_schema()
    await backfill_actors_if_needed()
    await migrate_legacy_actor_fks()
