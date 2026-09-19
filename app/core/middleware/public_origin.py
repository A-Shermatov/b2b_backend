from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.public_urls import (
    public_origin_from_request,
    reset_request_public_origin,
    set_request_public_origin,
)


class RequestPublicOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        origin = public_origin_from_request(request)
        token = set_request_public_origin(origin)
        try:
            return await call_next(request)
        finally:
            reset_request_public_origin(token)
