import re

from starlette.middleware.base import BaseHTTPMiddleware


class CollapseDuplicateSlashesMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        scope = request.scope
        path = scope.get("path") or ""
        if path and "//" in path:
            normalized = re.sub(r"/{2,}", "/", path)
            if normalized != path:
                scope["path"] = normalized
                scope["raw_path"] = normalized.encode("utf-8")
        return await call_next(request)
