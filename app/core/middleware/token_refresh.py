import logging

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.middleware._common import (
    MUTATING,
    SKIP_TOKEN_REFRESH_PATHS,
    decode_access_payload,
)
from app.core.security_core import create_access_token
from app.database import pool

logger = logging.getLogger(__name__)


class TokenRefreshMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method.upper() not in MUTATING:
            return response
        if request.url.path in SKIP_TOKEN_REFRESH_PATHS:
            return response
        if not pool.ready:
            return response

        payload = decode_access_payload(request.headers.get("authorization") or "")
        if not payload:
            return response
        try:
            user_id = int(payload.get("sub"))
        except (TypeError, ValueError):
            return response

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, role::text AS role, status::text AS status,
                           COALESCE(token_version, 0) AS token_version
                    FROM users WHERE id = $1
                    """,
                    user_id,
                )
            if not row or row["status"] == "blocked":
                return response
            payload_tv = int(payload.get("tv") or 0)
            db_tv = int(row["token_version"] or 0)
            if payload_tv != db_tv:
                return response
            token = create_access_token(
                str(row["id"]),
                {"role": row["role"], "tv": db_tv},
            )
            response.headers["Authorization"] = f"Bearer {token}"
            response.headers["X-Access-Token"] = token
        except Exception:
            logger.exception("Access token refresh failed")
        return response
