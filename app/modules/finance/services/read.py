from app.core.pagination import build_page, fetch_page
from app.modules.finance.services.common import destination_schema, invoice_schema, withdrawal_schema


class FinanceReadMixin:
    async def list_destinations(self, actor_id: int, page: int = 1, page_size: int = 20) -> dict:
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                "SELECT COUNT(*) FROM withdrawal_destinations WHERE actor_id = $1",
                """
                SELECT id, actor_id, type::text AS type, label, details, is_default
                FROM withdrawal_destinations
                WHERE actor_id = $1
                ORDER BY id
                """,
                [actor_id],
                page,
                page_size,
            )
        return build_page([destination_schema(row) for row in rows], total, page, page_size)

    async def list_withdrawals(self, actor_id: int, page: int = 1, page_size: int = 20) -> dict:
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                "SELECT COUNT(*) FROM withdrawals WHERE actor_id = $1",
                """
                SELECT id, actor_id, destination_id, amount, currency,
                       status::text AS status, bank, created_at, completed_at
                FROM withdrawals
                WHERE actor_id = $1
                ORDER BY created_at DESC
                """,
                [actor_id],
                page,
                page_size,
            )
        return build_page([withdrawal_schema(row) for row in rows], total, page, page_size)

    async def list_invoices(self, actor_id: int, page: int = 1, page_size: int = 20) -> dict:
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                "SELECT COUNT(*) FROM supplier_invoices WHERE actor_id = $1",
                """
                SELECT id, actor_id, contract_id, number, title, amount, currency,
                       status::text AS status, issued_at, due_at, paid_at
                FROM supplier_invoices
                WHERE actor_id = $1
                ORDER BY issued_at DESC
                """,
                [actor_id],
                page,
                page_size,
            )
        return build_page([invoice_schema(row) for row in rows], total, page, page_size)
