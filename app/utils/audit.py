import json
import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from app.utils.client_info import USER_AGENT_LIMIT

logger = logging.getLogger(__name__)

request_ip: ContextVar[str | None] = ContextVar("audit_request_ip", default=None)
request_user_agent: ContextVar[str | None] = ContextVar("audit_request_user_agent", default=None)
request_user_id: ContextVar[int | None] = ContextVar("audit_request_user_id", default=None)
#: Who is asking, as far as the request itself tells: set once per request by the
#: journal middleware and read by everything that writes to the journal.
request_facts: ContextVar["RequestFacts | None"] = ContextVar("audit_request_facts", default=None)

PATH_LIMIT = 500


@dataclass
class RequestFacts:
    """Where a request came from: address, device, visit."""

    request_id: uuid.UUID
    ip: str | None = None
    user_agent: str | None = None
    #: The browser's own short words about itself (Sec-CH-UA-* headers).
    hints: dict = field(default_factory=dict)
    language: str | None = None
    device_key: str | None = None
    #: One browser tab from opening to closing.
    session_key: str | None = None
    country: str | None = None
    user_id: int | None = None


INSERT_SQL = """
    INSERT INTO audit_logs (
        user_id, actor_id, company_id, action, resource_type, resource_id,
        details, ip_address, user_agent, created_at,
        method, path, status_code, duration_ms,
        request_id, device_key, session_key, country
    )
    VALUES (
        $1, $2, $3, $4, $5, $6,
        $7::jsonb, $8, $9, COALESCE($10, NOW()),
        $11, $12, $13, $14,
        $15, $16, $17, $18
    )
"""


def audit_row(
    *,
    user_id: int | None,
    actor_id: int | None,
    company_id: int | None,
    action: str,
    resource_type: str,
    resource_id: Any,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    created_at: datetime | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    request_id: uuid.UUID | None = None,
    device_key: str | None = None,
    session_key: str | None = None,
    country: str | None = None,
) -> tuple:
    """One journal row as the arguments of ``INSERT_SQL``."""
    return (
        user_id,
        actor_id,
        company_id,
        action[:100],
        resource_type[:100],
        str(resource_id)[:100],
        json.dumps(details, default=str) if details is not None else None,
        (ip_address[:50] if ip_address and ip_address != "-" else None),
        (user_agent[:USER_AGENT_LIMIT] if user_agent else None),
        created_at,
        (method[:10] if method else None),
        (path[:PATH_LIMIT] if path else None),
        status_code,
        duration_ms,
        request_id,
        (device_key[:64] if device_key else None),
        (session_key[:36] if session_key else None),
        (country[:2] if country else None),
    )


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
        method: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        facts: RequestFacts | None = None,
    ) -> None:
        # The device and the visit come with the request that is being served, so
        # an event ("proposal accepted") carries them without its caller's help.
        facts = facts or request_facts.get()
        await self.conn.execute(
            INSERT_SQL,
            *audit_row(
                user_id=user_id,
                actor_id=actor_id,
                company_id=company_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                ip_address=ip_address or (facts.ip if facts else None) or request_ip.get(),
                user_agent=user_agent or (facts.user_agent if facts else None) or request_user_agent.get(),
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                request_id=facts.request_id if facts else None,
                device_key=facts.device_key if facts else None,
                session_key=facts.session_key if facts else None,
                country=facts.country if facts else None,
            ),
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
