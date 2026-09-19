import json

from app.core.security_core import hash_password
from app.database import pool


async def fetchrow(query: str, *args):
    async with pool.connection() as conn:
        return await conn.fetchrow(query, *args)


async def fetch(query: str, *args):
    async with pool.connection() as conn:
        return await conn.fetch(query, *args)


async def fetchval(query: str, *args):
    async with pool.connection() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args):
    async with pool.connection() as conn:
        return await conn.execute(query, *args)


async def upsert_moderator(
    *,
    email: str = "moderator@example.com",
    password: str = "Moderator123!",
    first_name: str = "Test",
    last_name: str = "Moderator",
) -> int:
    pwd = hash_password(password)
    existing = await fetchval("SELECT id FROM users WHERE email = $1", email)
    if existing:
        await execute(
            """
            UPDATE users
            SET password_hash = $1,
                role = 'moderator'::user_role,
                status = 'active'::user_status,
                first_name = $2,
                last_name = $3,
                updated_at = NOW()
            WHERE id = $4
            """,
            pwd,
            first_name,
            last_name,
            existing,
        )
        return existing
    row = await fetchrow(
        """
        INSERT INTO users (
            email, password_hash, first_name, last_name, role, status,
            created_at, updated_at
        )
        VALUES (
            $1, $2, $3, $4, 'moderator'::user_role, 'active'::user_status,
            NOW(), NOW()
        )
        RETURNING id
        """,
        email,
        pwd,
        first_name,
        last_name,
    )
    return row["id"]


async def user_id_by_email(email: str) -> int:
    user_id = await fetchval("SELECT id FROM users WHERE email = $1", email)
    if user_id is None:
        raise AssertionError(f"user not found: {email}")
    return user_id


async def insert_catalog_item_report(
    *,
    item_id: int,
    reporter_user_id: int,
    reason: str,
    details: str,
    status: str = "open",
) -> int:
    return await fetchval(
        """
        INSERT INTO catalog_item_reports (
            item_id, reporter_user_id, reason, details, status, created_at
        )
        VALUES (
            $1, $2, $3::catalog_item_report_reason, $4,
            $5::catalog_item_report_status, NOW()
        )
        RETURNING id
        """,
        item_id,
        reporter_user_id,
        reason,
        details,
        status,
    )


async def insert_rfq_report(
    *,
    rfq_id: str,
    reporter_user_id: int,
    reason: str,
    details: str,
    status: str = "open",
) -> int:
    return await fetchval(
        """
        INSERT INTO rfq_reports (
            rfq_id, reporter_user_id, reason, details, status, created_at
        )
        VALUES (
            $1, $2, $3::rfq_report_reason, $4, $5::rfq_report_status, NOW()
        )
        RETURNING id
        """,
        rfq_id,
        reporter_user_id,
        reason,
        details,
        status,
    )


async def insert_proposal_report(
    *,
    proposal_id: int,
    reporter_user_id: int,
    reason: str,
    details: str,
    status: str = "open",
) -> int:
    return await fetchval(
        """
        INSERT INTO proposal_reports (
            proposal_id, reporter_user_id, reason, details, status, created_at
        )
        VALUES (
            $1, $2, $3::proposal_report_reason, $4,
            $5::proposal_report_status, NOW()
        )
        RETURNING id
        """,
        proposal_id,
        reporter_user_id,
        reason,
        details,
        status,
    )


async def insert_work_submission(
    *,
    contract_id: int,
    note: str,
    submission_type: str = "work",
    status: str = "pending",
    file_names: list[str] | None = None,
    assets: list | None = None,
) -> int:
    return await fetchval(
        """
        INSERT INTO work_submissions (
            contract_id, type, note, status, submitted_at, file_names, assets
        )
        VALUES (
            $1, $2::submission_type, $3, $4::submission_status,
            NOW(), $5, $6::jsonb
        )
        RETURNING id
        """,
        contract_id,
        submission_type,
        note,
        status,
        file_names or [],
        json.dumps(assets or []),
    )
