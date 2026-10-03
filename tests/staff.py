from uuid import uuid4

from tests.sql import fetch

API = "/api/v1"
STAFF_PASSWORD = "Staff12345!"


def unique_suffix() -> str:
    return uuid4().hex[:8]


async def permission_ids(*names: str) -> list[int]:
    rows = await fetch("SELECT id, name FROM permissions WHERE name = ANY($1::text[])", list(names))
    found = {row["name"]: row["id"] for row in rows}
    missing = set(names) - set(found)
    assert not missing, f"unknown permissions: {missing}"
    return [found[name] for name in names]


async def create_role_with_permissions(client, headers: dict, name: str, codes: list[str]) -> int:
    created = await client.post(
        f"{API}/admin/roles",
        json={"name": name, "description": "test role"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]
    if codes:
        saved = await client.put(
            f"{API}/admin/roles/{role_id}/matrix",
            json={"permission_ids": await permission_ids(*codes)},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
    return role_id


async def create_staff(client, headers: dict, role_id: int, email: str | None = None) -> dict:
    res = await client.post(
        f"{API}/admin/staff",
        json={
            "email": email or f"staff-{unique_suffix()}@example.com",
            "first_name": "Test",
            "last_name": "Staff",
            "password": STAFF_PASSWORD,
            "role_id": role_id,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def login_headers(client, email: str, password: str = STAFF_PASSWORD) -> dict:
    res = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
