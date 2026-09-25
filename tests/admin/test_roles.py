import pytest

from tests.sql import execute, fetchrow


@pytest.mark.asyncio
async def test_admin_role_matrix_crud(client, admin_auth):
    headers = admin_auth["headers"]
    catalog = await client.get("/api/v1/admin/permissions/matrix/catalog", headers=headers)
    assert catalog.status_code == 200, catalog.text
    rows = catalog.json()["rows"]
    resources = {row["resource"] for row in rows}
    assert "rfqs" in resources
    assert "roles" in resources

    created = await client.post(
        "/api/v1/admin/roles",
        json={"name": "viewer", "description": "read only"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]

    rfq_view = next(row["read"]["permission_id"] for row in rows if row["resource"] == "rfqs")
    saved = await client.put(
        f"/api/v1/admin/roles/{role_id}/matrix",
        json={"permission_ids": [rfq_view]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    rfq_row = next(row for row in saved.json()["rows"] if row["resource"] == "rfqs")
    assert rfq_row["read"]["enabled"] is True
    assert rfq_row["write"]["enabled"] is False

    buyer = await fetchrow("SELECT id FROM users WHERE email = $1", "buyer@example.com")
    assigned = await client.post(
        "/api/v1/admin/roles/assign",
        json={"user_id": buyer["id"], "role_id": role_id},
        headers=headers,
    )
    assert assigned.status_code == 204, assigned.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "buyer@example.com", "password": "Buyer123!", "role": "buyer"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    actor = next(a for a in me.json()["actors"] if a["side"] == "buyer")
    denied = await client.post(
        "/api/v1/buyer/payments/milestones/1/fund",
        headers={"Authorization": f"Bearer {token}", "X-Actor-Id": str(actor["id"])},
    )
    assert denied.status_code == 403, denied.text

    await execute(
        """
        UPDATE users
        SET role_id = (SELECT id FROM roles WHERE name = 'buyer')
        WHERE email = $1
        """,
        "buyer@example.com",
    )
    removed = await client.delete(f"/api/v1/admin/roles/{role_id}", headers=headers)
    assert removed.status_code == 204, removed.text
