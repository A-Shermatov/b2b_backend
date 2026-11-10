from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from app.utils.storage import INLINE_EXTENSIONS, is_private_key

_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline headers for API responses (JSON is never meant to be framed or sniffed)."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if not request.url.path.startswith(_DOCS_PREFIXES):
            headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        return response


class SafeStaticFiles(StaticFiles):
    """Local uploads: never let a stored file run as a page on our origin.

    Images, PDF and video are shown inline; anything else (incl. .html/.svg uploaded
    before type sniffing existed) is forced to download inside a CSP sandbox.
    """

    async def get_response(self, path, scope):
        # Identity documents, deal files and dispute evidence are only served
        # through signed links (GET /api/v1/files/...), never from this mount.
        if is_private_key(path.replace("\\", "/").lstrip("/")):
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        dot = path.rfind(".")
        ext = path[dot:].lower() if dot != -1 else ""
        if ext in INLINE_EXTENSIONS and ext != ".pdf":
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        elif ext != ".pdf":
            response.headers["Content-Disposition"] = "attachment"
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
