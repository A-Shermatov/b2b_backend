from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.deps import get_client_ip
from app.utils.redis_client import check_rate_limit

settings = get_settings()

_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/uploads",
)


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
            return await call_next(request)
        # WebSockets / long-lived streams are not rate-limited here
        if path.startswith("/api/v1/ws/"):
            return await call_next(request)

        ip = await get_client_ip(request)
        allowed = await check_rate_limit(
            f"global:{ip}",
            settings.rate_limit_global_per_minute,
            60,
        )
        if not allowed:
            # Imported here: the journal middleware imports from this package too.
            from app.core.middleware.http_audit import note_rate_limited

            note_rate_limited(request)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try again later."},
            )
        return await call_next(request)
