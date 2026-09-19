from app.modules.finance.services.common import destination_schema, invoice_schema, withdrawal_schema


class FinanceReadMixin:
    async def list_destinations(self, actor_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, actor_id, type::text AS type, label, details, is_default
                FROM withdrawal_destinations
                WHERE actor_id = $1
                ORDER BY id
                """,
                actor_id,
            )
        return [destination_schema(row) for row in rows]

    async def list_withdrawals(self, actor_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, actor_id, destination_id, amount, currency,
                       status::text AS status, created_at, completed_at
                FROM withdrawals
                WHERE actor_id = $1
                ORDER BY created_at DESC
                """,
                actor_id,
            )
        return [withdrawal_schema(row) for row in rows]

    async def list_invoices(self, actor_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, actor_id, contract_id, number, title, amount, currency,
                       status::text AS status, issued_at, due_at, paid_at
                FROM supplier_invoices
                WHERE actor_id = $1
                ORDER BY issued_at DESC
                """,
                actor_id,
            )
        return [invoice_schema(row) for row in rows]
