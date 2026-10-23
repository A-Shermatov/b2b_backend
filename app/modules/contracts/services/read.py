from app.core.pagination import build_page, fetch_page
from app.modules.contracts.services.common import (
    ensure_party,
    load_contract_row,
    load_contract_schema,
)


class ContractReadMixin:
    async def list_for_actor(
        self,
        actor_id: int,
        actor_type: str,
        page: int = 1,
        page_size: int = 20,
        *,
        status: str | None = None,
    ) -> dict:
        column = "buyer_actor_id" if actor_type == "buyer" else "supplier_actor_id"
        clauses = [f"c.{column} = $1"]
        params: list = [actor_id]
        if status:
            params.append(status)
            clauses.append(f"c.status::text = ${len(params)}")
        where = " AND ".join(clauses)
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                f"SELECT COUNT(*) FROM contracts c WHERE {where}",
                f"""
                SELECT c.id
                FROM contracts c
                WHERE {where}
                ORDER BY c.created_at DESC
                """,
                params,
                page,
                page_size,
            )
            items = [await load_contract_schema(conn, row["id"]) for row in rows]
        return build_page(items, total, page, page_size)

    async def get(self, contract_id: int, actor_id: int) -> dict:
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await ensure_party(row, actor_id)
            return await load_contract_schema(conn, contract_id)
