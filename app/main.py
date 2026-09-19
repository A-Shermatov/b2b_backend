import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.core.middleware import (
    AccessLogMiddleware,
    CollapseDuplicateSlashesMiddleware,
    HttpAuditMiddleware,
    RequestPublicOriginMiddleware,
    TokenRefreshMiddleware,
)
from app.database import pool
from app.modules.reviews.router import register as register_reviews
from src.api.v1.router import api_router
from src.db.schema import backfill_actors_if_needed, ensure_schema

logger = logging.getLogger(__name__)
settings = get_settings()


async def init_db_schema(retries: int = 5, delay_seconds: float = 2.0) -> None:
    if settings.app_env != "development":
        return

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await ensure_schema()
            logger.info("Database schema ready")
            await backfill_actors_if_needed()
            return
        except (OperationalError, OSError, ConnectionError) as exc:
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
    await init_db_schema()
    await pool.init()
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(HttpAuditMiddleware)
app.add_middleware(TokenRefreshMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestPublicOriginMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Authorization", "X-Access-Token"],
)
app.add_middleware(CollapseDuplicateSlashesMiddleware)

upload_dir = Path(settings.local_upload_dir)
upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

app.include_router(api_router, prefix=settings.api_v1_prefix)
register_reviews(app, prefix=settings.api_v1_prefix)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}
