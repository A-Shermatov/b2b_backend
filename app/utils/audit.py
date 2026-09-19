import json
from typing import Any

import asyncpg


class AuditLogger:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn

    async def log_action(
        self,
        *,
        user_id: int | None,
        actor_id: int | None,
        company_id: int | None,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO audit_logs (
                user_id, actor_id, company_id, action, resource_type, resource_id,
                details, ip_address, created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, NOW())
            """,
            user_id,
            actor_id,
            company_id,
            action[:100],
            resource_type[:100],
            str(resource_id)[:100],
            json.dumps(details, default=str) if details is not None else None,
            (ip_address or None),
        )
