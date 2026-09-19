import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.middleware._common import (
    MUTATING,
    SKIP_AUDIT_PATHS,
    SKIP_LOG_PREFIXES,
    actor_side_from_path,
    decode_access_payload,
    parse_optional_int,
    redact_data,
    resource_id_from_path,
)
from app.database import pool
from app.utils.audit import AuditLogger
from app.utils.request_meta import client_ip

logger = logging.getLogger(__name__)
_BODY_LIMIT = 4000


def _skip_path(path: str) -> bool:
    if any(path == prefix or path.startswith(prefix) for prefix in SKIP_LOG_PREFIXES):
        return True
    if path in SKIP_AUDIT_PATHS:
        return True
    if path.startswith("/ws"):
        return True
    return False


def _should_write(method: str, status_code: int) -> bool:
    if method.upper() in MUTATING:
        return True
    return status_code >= 400


async def _json_body(request) -> dict | None:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return None
    try:
        raw = await request.body()
    except Exception:
        return None
    if not raw or len(raw) > _BODY_LIMIT:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, (dict, list)):
        return None
    return redact_data(data)


class HttpAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        method = request.method.upper()
        body = None
        if not _skip_path(path) and method in MUTATING:
            body = await _json_body(request)

        response = await call_next(request)
        if _skip_path(path) or not _should_write(method, int(response.status_code)):
            return response
        if not pool.ready:
            return response

        payload = decode_access_payload(request.headers.get("authorization") or "")
        try:
            user_id = int(payload["sub"]) if payload and payload.get("sub") is not None else None
        except (TypeError, ValueError):
            user_id = None

        actor_id = parse_optional_int(request.headers.get("x-actor-id"))
        company_id = parse_optional_int(request.headers.get("x-company-id"))
        side = actor_side_from_path(path)
        details = {
            "path": path,
            "query": dict(request.query_params),
            "side": side,
            "status_code": int(response.status_code),
        }
        if body is not None:
            details["request_body"] = body

        try:
            async with pool.acquire() as conn:
                await AuditLogger(conn).log_action(
                    user_id=user_id,
                    actor_id=actor_id,
                    company_id=company_id,
                    action=f"http.{method.lower()}",
                    resource_type=side if side != "-" else "http",
                    resource_id=resource_id_from_path(path),
                    details=details,
                    ip_address=client_ip(request),
                )
        except Exception:
            logger.exception("HTTP audit write failed")
        return response
