import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncpg

from app.config import get_settings
from app.core.middleware import (
    AccessLogMiddleware,
    CollapseDuplicateSlashesMiddleware,
    GlobalRateLimitMiddleware,
    HttpAuditMiddleware,
    RequestPublicOriginMiddleware,
    TokenRefreshMiddleware,
)
from app.core.middleware.security_headers import SafeStaticFiles, SecurityHeadersMiddleware
from app.database import pool
from app.modules.admin.router import register as register_admin
from app.modules.approvals.router import register as register_approvals
from app.modules.auth.router import register as register_auth
from app.modules.catalog.router import register as register_catalog
from app.modules.companies.router import register as register_companies
from app.modules.finance.router import register as register_finance
from app.modules.contracts.router import register as register_contracts
from app.modules.direct_chats.router import register as register_direct_chats
from app.modules.nav.router import register as register_nav
from app.modules.notifications.router import register as register_notifications
from app.modules.payments.router import register as register_payments
from app.modules.proposals.router import register as register_proposals
from app.modules.rfqs.router import register as register_rfqs
from app.modules.reviews.router import register as register_reviews
from app.modules.roles.router import register as register_roles
from app.modules.settings.router import register as register_settings
from app.modules.subscription.router import register as register_subscription
from app.modules.support.router import register as register_support
from app.modules.verification.router import register as register_verification
from app.db.schema import backfill_actors_if_needed, ensure_schema

logger = logging.getLogger(__name__)
settings = get_settings()

# Ensure app.* INFO (incl. TOTP diagnostics) reaches the container stdout.
_app_log = logging.getLogger("app")
_app_log.setLevel(logging.INFO)
if not _app_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    )
    _app_log.addHandler(_handler)
_app_log.propagate = False


async def init_db_schema(retries: int = 5, delay_seconds: float = 2.0) -> None:
    if settings.app_env != "development":
        return

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await ensure_schema()
            logger.info("Database schema ready")
            await pool.init()
            await backfill_actors_if_needed()
            return
        except (OSError, ConnectionError, TimeoutError, asyncpg.PostgresError) as exc:
            last_error = exc
            logger.warning(
                "Database not ready (attempt %s/%s): %s",
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                await asyncio.sleep(delay_seconds)

    raise RuntimeError(
        "Cannot connect to PostgreSQL. "
        "Run `docker compose up -d` in backend/ and ensure DATABASE_URL uses port 5433 "
        "(port 5432 may be used by a local PostgreSQL install)."
    ) from last_error


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.alif_requested and not settings.alif_enabled:
        logger.warning(
            "PAYMENT_PROVIDER=alif but ALIF_TERMINAL_KEY/ALIF_TERMINAL_PASSWORD are not set; "
            "milestone funding will fail until they are configured"
        )
    await init_db_schema()
    if not pool.ready:
        await pool.init()
    try:
        yield
    finally:
        await pool.close()


# Interactive docs and the OpenAPI schema only in dev environments.
_docs_enabled = settings.is_dev_env
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

app.add_middleware(HttpAuditMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TokenRefreshMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestPublicOriginMiddleware)
app.add_middleware(GlobalRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Authorization", "X-Access-Token"],
)
app.add_middleware(CollapseDuplicateSlashesMiddleware)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_request: Request, exc: RequestValidationError):
    # FastAPI's default handler fails with 500 when the rejected body is not UTF-8.
    errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={bytes: lambda value: value.decode("utf-8", errors="replace")},
    )
    return JSONResponse(status_code=422, content={"detail": errors})


upload_dir = Path(settings.local_upload_dir)
upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", SafeStaticFiles(directory=str(upload_dir)), name="uploads")

register_auth(app, prefix=settings.api_v1_prefix)
register_companies(app, prefix=settings.api_v1_prefix)
register_reviews(app, prefix=settings.api_v1_prefix)
register_catalog(app, prefix=settings.api_v1_prefix)
register_notifications(app, prefix=settings.api_v1_prefix)
register_nav(app, prefix=settings.api_v1_prefix)
register_subscription(app, prefix=settings.api_v1_prefix)
register_rfqs(app, prefix=settings.api_v1_prefix)
register_proposals(app, prefix=settings.api_v1_prefix)
register_contracts(app, prefix=settings.api_v1_prefix)
register_direct_chats(app, prefix=settings.api_v1_prefix)
register_support(app, prefix=settings.api_v1_prefix)
register_approvals(app, prefix=settings.api_v1_prefix)
register_payments(app, prefix=settings.api_v1_prefix)
register_finance(app, prefix=settings.api_v1_prefix)
register_admin(app, prefix=settings.api_v1_prefix)
register_roles(app, prefix=settings.api_v1_prefix)
register_settings(app, prefix=settings.api_v1_prefix)
register_verification(app, prefix=settings.api_v1_prefix)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}
