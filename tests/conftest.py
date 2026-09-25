import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import pool
from app.main import app
from app.utils import redis_client

API = "/api/v1"


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
