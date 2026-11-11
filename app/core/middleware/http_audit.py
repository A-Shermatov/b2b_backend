import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.journal import journal
from app.core.middleware._common import (
    API_PREFIX,
    MUTATING,
    SKIP_AUDIT_PATHS,
    SKIP_JOURNAL_PATHS,
    SKIP_LOG_PREFIXES,
    actor_side_from_path,
    decode_access_payload,
    parse_optional_int,
    redact_data,
    resource_id_from_path,
    safe_path,
)
from app.database import pool
from app.utils.audit import (
    AuditLogger,
    RequestFacts,
    request_facts,
    request_ip,
    request_user_agent,
    request_user_id,
)
from app.utils.client_info import clean_uuid, device_key_for, header_hints
from app.utils.request_meta import client_country, client_ip

logger = logging.getLogger(__name__)
_BODY_LIMIT = 4000
REQUEST_ID_HEADER = "X-Request-Id"
#: The id a browser gives itself, and the id of one visit (a tab until it is closed).
DEVICE_HEADER = "x-device-id"
VISIT_HEADER = "x-visit-id"


def _skip_path(path: str) -> bool:
    if any(path == prefix or path.startswith(prefix) for prefix in SKIP_LOG_PREFIXES):
        return True
    if path in SKIP_AUDIT_PATHS or path in SKIP_JOURNAL_PATHS:
        return True
    if path.startswith("/ws") or "/ws/" in path:
        return True
    return False


def _is_view(method: str, status_code: int) -> bool:
    """A plain read that went well: most of the traffic, written in batches."""
    return method not in MUTATING and status_code < 400


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


def facts_of(request) -> RequestFacts:
    """Who is asking: address, device, visit and (from the access token) the person."""
    headers = request.headers
    payload = decode_access_payload(headers.get("authorization") or "")
    try:
        user_id = int(payload["sub"]) if payload and payload.get("sub") is not None else None
    except (TypeError, ValueError):
        user_id = None
    ip = client_ip(request)
    user_agent = headers.get("user-agent")
    language = headers.get("accept-language")
    return RequestFacts(
        request_id=uuid.uuid4(),
        ip=ip,
        user_agent=user_agent,
        hints=header_hints(headers),
        language=language,
        device_key=device_key_for(headers.get(DEVICE_HEADER), ip, user_agent, language),
        session_key=clean_uuid(headers.get(VISIT_HEADER)),
        country=client_country(request),
        user_id=user_id,
    )


def note_rate_limited(request) -> None:
    """A request the limiter turned away.

    A flood is thousands of these, so a device gets one row per repeat window
    with the number of refusals, not a row each.
    """
    facts = facts_of(request)
    path = safe_path(request.url.path)
    side = actor_side_from_path(path)
    journal.touch(facts)
    journal.view(
        facts,
        method=request.method.upper(),
        path=path,
        query=None,
        status_code=429,
        duration_ms=0,
        actor_id=None,
        company_id=None,
        resource_type=side if side != "-" else "http",
        resource_id="-",
        repeat_key=("rate-limited", facts.device_key),
    )


class HttpAuditMiddleware(BaseHTTPMiddleware):
    """Writes every request to the journal.

    Changes, refusals and errors are written at once, with the data that was
    sent (secrets hidden). Plain views go through the journal's background
    writer. Either way the device the request came from is noted.
    """

    async def dispatch(self, request, call_next):
        # One-time tokens in the address (an invitation link) are not kept.
        path = safe_path(request.url.path)
        method = request.method.upper()
        skip = _skip_path(path)
        body = None
        if not skip and method in MUTATING:
            body = await _json_body(request)

        facts = facts_of(request)
        if not path.startswith(API_PREFIX):
            # Whatever knocks on addresses the API does not have (a scanner trying
            # /wp-login.php) is written down, but gets no entry in the registry
            # of devices: that one is for clients of the platform.
            facts.device_key = None
            facts.session_key = None
        ip_token = request_ip.set(facts.ip)
        agent_token = request_user_agent.set(facts.user_agent)
        user_token = request_user_id.set(facts.user_id)
        facts_token = request_facts.set(facts)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_ip.reset(ip_token)
            request_user_agent.reset(agent_token)
            request_user_id.reset(user_token)
            request_facts.reset(facts_token)
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = str(facts.request_id)

        # Uploaded files and health checks are not somebody's action.
        if any(path == prefix or path.startswith(prefix) for prefix in SKIP_LOG_PREFIXES):
            return response
        journal.touch(facts)
        if skip:
            return response

        status_code = int(response.status_code)
        actor_id = parse_optional_int(request.headers.get("x-actor-id"))
        company_id = parse_optional_int(request.headers.get("x-company-id"))
        side = actor_side_from_path(path)
        resource_type = side if side != "-" else "http"
        query = redact_data(dict(request.query_params)) or None

        if _is_view(method, status_code):
            journal.view(
                facts,
                method=method,
                path=path,
                query=query,
                status_code=status_code,
                duration_ms=duration_ms,
                actor_id=actor_id,
                company_id=company_id,
                resource_type=resource_type,
                resource_id=resource_id_from_path(path),
            )
            return response
        if not pool.ready:
            return response

        details = {
            "path": path,
            "query": query or {},
            "side": side,
            "status_code": status_code,
        }
        if body is not None:
            details["request_body"] = body

        try:
            async with pool.acquire() as conn:
                await AuditLogger(conn).log_action(
                    user_id=facts.user_id,
                    actor_id=actor_id,
                    company_id=company_id,
                    action=f"http.{method.lower()}",
                    resource_type=resource_type,
                    resource_id=resource_id_from_path(path),
                    details=details,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    facts=facts,
                )
        except Exception:
            logger.exception("HTTP audit write failed")
        return response
