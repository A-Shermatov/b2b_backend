"""Cross-request locks for money operations.

Bank calls (confirm-delivery, cancel/marketplace) run outside DB transactions, so
row locks cannot protect them. A PostgreSQL advisory lock per contract, held on a
dedicated connection for the whole operation, serialises acceptance, payouts,
refunds and disputes of one deal. The lock is taken with ``pg_try_advisory_lock``:
a concurrent request fails fast with 409 instead of waiting (and instead of
holding a pooled connection while waiting for another one).
"""

from contextlib import asynccontextmanager

import asyncpg

from app.config import get_settings
from app.core.exceptions import ConflictError

CONTRACT_LOCK_NAMESPACE = 7301
CONTRACT_BUSY_MESSAGE = (
    "По этой сделке уже выполняется операция. Повторите через несколько секунд."
)


def _dsn() -> str:
    from app.database import DatabasePool

    return DatabasePool.dsn(get_settings().database_url)


@asynccontextmanager
async def contract_lock(contract_id: int):
    conn = await asyncpg.connect(_dsn(), timeout=10)
    try:
        acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1::int, $2::int)",
            CONTRACT_LOCK_NAMESPACE,
            int(contract_id),
        )
        if not acquired:
            raise ConflictError(CONTRACT_BUSY_MESSAGE)
        try:
            yield
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1::int, $2::int)",
                CONTRACT_LOCK_NAMESPACE,
                int(contract_id),
            )
    finally:
        await conn.close()


async def ensure_contract_not_busy(conn, contract_id: int) -> None:
    """Inside a transaction: fail if a money operation on the contract is running.

    The transaction-level lock is released on commit/rollback.
    """
    acquired = await conn.fetchval(
        "SELECT pg_try_advisory_xact_lock($1::int, $2::int)",
        CONTRACT_LOCK_NAMESPACE,
        int(contract_id),
    )
    if not acquired:
        raise ConflictError(CONTRACT_BUSY_MESSAGE)
