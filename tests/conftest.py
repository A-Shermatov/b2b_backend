import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_DB_SUFFIX = "_test"
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _with_database_name(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def _resolve_test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL") or dotenv_values(BACKEND_ROOT / ".env").get(
        "DATABASE_URL"
    )
    if not base:
        pytest.exit("DATABASE_URL or TEST_DATABASE_URL must be set to run tests", returncode=2)
    name = _database_name(base)
    if name.endswith(TEST_DB_SUFFIX):
        return base
    return _with_database_name(base, f"{name}{TEST_DB_SUFFIX}")


TEST_DATABASE_URL = _resolve_test_database_url()
TEST_DATABASE_NAME = _database_name(TEST_DATABASE_URL)
if not TEST_DATABASE_NAME.endswith(TEST_DB_SUFFIX) or not DB_NAME_RE.match(TEST_DATABASE_NAME):
    pytest.exit(
        f"Refusing to run tests against non-test database '{TEST_DATABASE_NAME}'. "
        f"The database name must end with '{TEST_DB_SUFFIX}'.",
        returncode=2,
    )
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.database import DatabasePool, pool
from app.main import app
from app.utils import redis_client

API = "/api/v1"


async def _ensure_test_database() -> None:
    dsn = DatabasePool.dsn(TEST_DATABASE_URL)
    admin_conn = await asyncpg.connect(_with_database_name(dsn, "postgres"))
    try:
        exists = await admin_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DATABASE_NAME
        )
        if not exists:
            await admin_conn.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')
    finally:
        await admin_conn.close()

    from scripts.seed import seed

    await seed()


def pytest_sessionstart(session):
    asyncio.run(_ensure_test_database())


@pytest.fixture
async def client(monkeypatch):
    from app.db.schema import prepare_database

    await prepare_database()
    if not pool.ready:
        await pool.init()

    async def _allow_rate_limit(*_args, **_kwargs):
        return True

    monkeypatch.setattr(redis_client, "check_rate_limit", _allow_rate_limit)
    monkeypatch.setattr("app.modules.auth.api.create.check_rate_limit", _allow_rate_limit)
    monkeypatch.setattr("app.modules.auth.api.update.check_rate_limit", _allow_rate_limit)
    monkeypatch.setattr(
        "app.core.middleware.rate_limit.check_rate_limit", _allow_rate_limit
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await pool.close()
    redis_client._redis_client = None
    redis_client._memory_rate_limit.clear()


async def _login(
    client: AsyncClient,
    email: str,
    password: str,
    *,
    side: str,
) -> dict:
    res = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password, "role": side},
    )
    assert res.status_code == 200, res.text
    tokens = res.json()
    me_res = await client.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_res.status_code == 200, me_res.text
    me = me_res.json()
    actor = next(
        (a for a in me["actors"] if a.get("side") == side),
        None,
    )
    actor_id = actor["id"] if actor else None
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    if actor_id:
        headers["X-Actor-Id"] = str(actor_id)
    return {
        "token": tokens["access_token"],
        "actor_id": actor_id,
        "headers": headers,
        "me": me,
    }


@pytest.fixture
async def buyer_auth(client: AsyncClient):
    return await _login(client, "buyer@example.com", "Buyer123!", side="buyer")


@pytest.fixture
async def supplier_auth(client: AsyncClient):
    return await _login(client, "supplier@example.com", "Supplier123!", side="supplier")


@pytest.fixture
async def admin_auth(client: AsyncClient):
    res = await client.post(
        f"{API}/auth/login",
        json={"email": "admin@example.com", "password": "Admin123!"},
    )
    assert res.status_code == 200, res.text
    tokens = res.json()
    return {
        "token": tokens["access_token"],
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }
