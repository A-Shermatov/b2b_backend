"""Persist payment-gateway HTTP traffic for audit.

Request is written before the call; response is written as received, before
business processing. Uses its own DB connection so logs survive rollbacks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.database import pool

logger = logging.getLogger(__name__)

_REDACT_KEYS = frozenset({"password", "alif_terminal_password", "secret", "client_secret"})


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def sanitize_for_log(payload: Any) -> Any:
    """Keep payload as-is except redact obvious secrets."""
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if str(key).lower() in _REDACT_KEYS:
                out[key] = "***"
            else:
                out[key] = sanitize_for_log(value)
        return out
    if isinstance(payload, list):
        return [sanitize_for_log(item) for item in payload]
    return _json_safe(payload)


def extract_order_id(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in ("order_id", "orderId", "order"):
        value = body.get(key)
        if value not in (None, ""):
            return str(value)[:64]
    return None


async def log_gateway_request(
    *,
    gateway: str,
    direction: str,
    operation: str,
    method: str | None = None,
    url: str | None = None,
    request_headers: dict | None = None,
    request_body: Any = None,
    order_id: str | None = None,
) -> int | None:
    resolved_order = order_id or extract_order_id(request_body)
    try:
        if not pool.ready:
            await pool.init()
        async with pool.connection() as conn:
            return await conn.fetchval(
                """
                INSERT INTO payment_gateway_http_logs (
                    gateway, direction, operation, order_id, method, url,
                    request_headers, request_body
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7::jsonb, $8::jsonb
                )
                RETURNING id
                """,
                gateway,
                direction,
                operation[:64],
                resolved_order,
                method,
                url,
                json.dumps(sanitize_for_log(request_headers), ensure_ascii=False, default=str)
                if request_headers is not None
                else None,
                json.dumps(sanitize_for_log(request_body), ensure_ascii=False, default=str)
                if request_body is not None
                else None,
            )
    except Exception:
        logger.exception(
            "Failed to persist payment gateway request log (%s %s)", gateway, operation
        )
        return None


async def log_gateway_response(
    log_id: int | None,
    *,
    response_status: int | None = None,
    response_body: Any = None,
    response_raw: str | None = None,
    error: str | None = None,
    order_id: str | None = None,
) -> None:
    if log_id is None:
        return
    body_json = None
    raw = response_raw
    if response_body is not None:
        try:
            body_json = json.dumps(
                sanitize_for_log(response_body), ensure_ascii=False, default=str
            )
        except TypeError:
            raw = raw or str(response_body)
    try:
        if not pool.ready:
            await pool.init()
        async with pool.connection() as conn:
            await conn.execute(
                """
                UPDATE payment_gateway_http_logs
                SET response_status = $2,
                    response_body = $3::jsonb,
                    response_raw = $4,
                    error = $5,
                    order_id = COALESCE($6, order_id),
                    responded_at = NOW()
                WHERE id = $1
                """,
                log_id,
                response_status,
                body_json,
                (raw[:20000] if raw else None),
                (error[:2000] if error else None),
                order_id,
            )
    except Exception:
        logger.exception("Failed to persist payment gateway response log id=%s", log_id)


async def log_gateway_inbound(
    *,
    gateway: str,
    operation: str,
    request_body: Any,
    request_headers: dict | None = None,
    url: str | None = None,
    order_id: str | None = None,
) -> int | None:
    """Log an inbound bank callback/webhook as received, before processing."""
    return await log_gateway_request(
        gateway=gateway,
        direction="inbound",
        operation=operation,
        method="POST",
        url=url,
        request_headers=request_headers,
        request_body=request_body,
        order_id=order_id,
    )
