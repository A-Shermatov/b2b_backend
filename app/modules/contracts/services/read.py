from app.modules.contracts.services.common import (
    CONTRACT_COLUMNS,
    ensure_party,
    load_contract_row,
    load_contract_schema,
)


class ContractReadMixin:
    async def list_for_actor(self, actor_id: int, actor_type: str) -> list:
        column = "buyer_actor_id" if actor_type == "buyer" else "supplier_actor_id"
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {CONTRACT_COLUMNS}
                FROM contracts c
                WHERE c.{column} = $1
                ORDER BY c.created_at DESC
                """,
                actor_id,
            )
            return [await load_contract_schema(conn, row["id"]) for row in rows]

    async def get(self, contract_id: int, actor_id: int) -> dict:
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await ensure_party(row, actor_id)
            return await load_contract_schema(conn, contract_id)
