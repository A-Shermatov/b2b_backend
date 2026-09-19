import logging
import threading
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.middleware._common import (
    SKIP_LOG_PREFIXES,
    actor_side_from_path,
    decode_access_payload,
    parse_optional_int,
)
from app.utils.request_meta import client_ip

access_logger = logging.getLogger("app.access")
access_logger.setLevel(logging.INFO)
if not access_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    access_logger.addHandler(handler)
access_logger.propagate = False

_HEADER = (
    f"{'TIME':<19}  {'METHOD':<7}  {'STATUS':<6}  {'IP':<15}  "
    f"{'USER_ID':<8}  {'ACTOR_ID':<8}  {'COMPANY':<8}  {'SIDE':<8}  PATH"
)
_RULE = "-" * len(_HEADER)
_rows = 0
_lock = threading.Lock()


def _skip(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in SKIP_LOG_PREFIXES)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if _skip(path):
            return response

        payload = decode_access_payload(request.headers.get("authorization") or "")
        user_id = payload.get("sub") if payload else None
        actor_id = parse_optional_int(request.headers.get("x-actor-id"))
        company_id = parse_optional_int(request.headers.get("x-company-id"))
        side = actor_side_from_path(path)

        global _rows
        row = (
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'):<19}  "
            f"{request.method.upper():<7}  "
            f"{int(response.status_code):<6}  "
            f"{client_ip(request):<15}  "
            f"{str(user_id or '-'):<8}  "
            f"{str(actor_id or '-'):<8}  "
            f"{str(company_id or '-'):<8}  "
            f"{side:<8}  "
            f"{path}"
        )
        with _lock:
            reprint = _rows % 20 == 0
            _rows += 1
        if reprint:
            access_logger.info(_RULE)
            access_logger.info(_HEADER)
            access_logger.info(_RULE)
        access_logger.info(row)
        return response
