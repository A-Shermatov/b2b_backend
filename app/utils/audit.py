import json
import logging
from contextvars import ContextVar
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

USER_AGENT_LIMIT = 500

request_ip: ContextVar[str | None] = ContextVar("audit_request_ip", default=None)
request_user_agent: ContextVar[str | None] = ContextVar("audit_request_user_agent", default=None)
request_user_id: ContextVar[int | None] = ContextVar("audit_request_user_id", default=None)


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
        user_agent: str | None = None,
    ) -> None:
        ip = ip_address or request_ip.get()
        agent = user_agent or request_user_agent.get()
        await self.conn.execute(
            """
            INSERT INTO audit_logs (
                user_id, actor_id, company_id, action, resource_type, resource_id,
                details, ip_address, user_agent, created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, NOW())
            """,
            user_id,
            actor_id,
            company_id,
            action[:100],
            resource_type[:100],
            str(resource_id)[:100],
            json.dumps(details, default=str) if details is not None else None,
            (ip[:50] if ip and ip != "-" else None),
            (agent[:USER_AGENT_LIMIT] if agent else None),
        )


async def log_event(
    conn: asyncpg.Connection,
    action: str,
    resource_type: str,
    resource_id: Any,
    *,
    user_id: int | None = None,
    actor_id: int | None = None,
    company_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort semantic audit row; never breaks the caller's transaction."""
    if user_id is None:
        user_id = request_user_id.get()
    try:
        if conn.is_in_transaction():
            async with conn.transaction():
                await _write(conn, action, resource_type, resource_id, user_id, actor_id, company_id, details)
        else:
            await _write(conn, action, resource_type, resource_id, user_id, actor_id, company_id, details)
    except Exception:
        logger.exception("Audit event %s failed", action)


async def _write(conn, action, resource_type, resource_id, user_id, actor_id, company_id, details):
    await AuditLogger(conn).log_action(
        user_id=user_id,
        actor_id=actor_id,
        company_id=company_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details,
    )
