import pytest

from tests.sql import execute, fetchval, upsert_moderator, upsert_staff_user
from tests.staff import (
    STAFF_PASSWORD,
    create_role_with_permissions,
    create_staff,
    login_headers,
    unique_suffix,
)

API = "/api/v1"


@pytest.mark.asyncio
async def test_admin_creates_lists_and_updates_staff(client, admin_auth):
    headers = admin_auth["headers"]
    first_role = await create_role_with_permissions(
        client, headers, f"staff-a-{unique_suffix()}", ["admin_disputes.view"]
    )
    second_role = await create_role_with_permissions(
        client, headers, f"staff-b-{unique_suffix()}", ["admin_reports.view"]
    )
    staff = await create_staff(client, headers, first_role)
    assert staff["role_id"] == first_role
    assert staff["status"] == "active"

    listed = await client.get(
        f"{API}/admin/staff", params={"query": staff["email"]}, headers=headers
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [staff["id"]]

    duplicate = await client.post(
        f"{API}/admin/staff",
        json={
            "email": staff["email"],
            "first_name": "Dup",
            "last_name": "Staff",
            "password": STAFF_PASSWORD,
            "role_id": first_role,
        },
        headers=headers,
    )
    assert duplicate.status_code == 409, duplicate.text

    new_password = "Changed12345!"
    updated = await client.patch(
        f"{API}/admin/staff/{staff['id']}",
        json={"role_id": second_role, "first_name": "Renamed", "password": new_password},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["role_id"] == second_role
    assert updated.json()["first_name"] == "Renamed"

    staff_headers = await login_headers(client, staff["email"], new_password)
    me = await client.get(f"{API}/auth/me", headers=staff_headers)
    assert me.json()["permissions"] == ["admin_reports.view"]

    audit = await fetchval(
        """
        SELECT action FROM audit_logs
        WHERE resource_type = 'user' AND resource_id = $1 AND action LIKE 'admin.staff.%'
        ORDER BY id DESC LIMIT 1
        """,
        str(staff["id"]),
    )
    assert audit == "admin.staff.update"


@pytest.mark.asyncio
async def test_staff_list_excludes_marketplace_users(client, admin_auth):
    res = await client.get(
        f"{API}/admin/staff", params={"query": "buyer@example.com"}, headers=admin_auth["headers"]
    )
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


@pytest.mark.asyncio
async def test_admin_cannot_grant_permissions_they_lack(client, admin_auth):
    headers = admin_auth["headers"]
    resource = f"admin_zz{unique_suffix()}"
    permission_id = await fetchval(
        """
        INSERT INTO permissions (name, resource, action)
        VALUES ($1, $2, 'view')
        RETURNING id
        """,
        f"{resource}.view",
        resource,
    )
    try:
        role_id = await create_role_with_permissions(
            client, headers, f"escalate-{unique_suffix()}", []
        )
        res = await client.put(
            f"{API}/admin/roles/{role_id}/matrix",
            json={"permission_ids": [permission_id]},
            headers=headers,
        )
        assert res.status_code == 403, res.text
    finally:
        await execute("DELETE FROM permissions WHERE id = $1", permission_id)


@pytest.mark.asyncio
async def test_admin_cannot_create_or_change_admins(client, admin_auth):
    headers = admin_auth["headers"]
    admin_role_id = await fetchval("SELECT id FROM roles WHERE name = 'admin'")
    email = f"new-admin-{unique_suffix()}@example.com"

    create_admin = await client.post(
        f"{API}/admin/staff",
        json={
            "email": email,
            "first_name": "New",
            "last_name": "Admin",
            "password": STAFF_PASSWORD,
            "role_id": admin_role_id,
        },
        headers=headers,
    )
    assert create_admin.status_code == 403, create_admin.text
    assert await fetchval("SELECT id FROM users WHERE email = $1", email) is None

    other_admin_id = await upsert_staff_user(
        email="other-admin@example.com",
        password=STAFF_PASSWORD,
        role_name="admin",
        platform_role="admin",
    )
    block_admin = await client.patch(
        f"{API}/admin/staff/{other_admin_id}",
        json={"status": "blocked"},
        headers=headers,
    )
    assert block_admin.status_code == 403, block_admin.text

    me = await client.get(f"{API}/auth/me", headers=headers)
    self_update = await client.patch(
        f"{API}/admin/staff/{me.json()['user']['id']}",
        json={"first_name": "Self"},
        headers=headers,
    )
    assert self_update.status_code == 403, self_update.text


@pytest.mark.asyncio
async def test_moderator_cannot_manage_staff(client, admin_auth):
    role_id = await create_role_with_permissions(
        client, admin_auth["headers"], f"mod-target-{unique_suffix()}", ["admin_disputes.view"]
    )
    target = await create_staff(client, admin_auth["headers"], role_id)

    await upsert_moderator()
    moderator_headers = await login_headers(client, "moderator@example.com", "Moderator123!")

    create_res = await client.post(
        f"{API}/admin/staff",
        json={
            "email": f"mod-created-{unique_suffix()}@example.com",
            "first_name": "Mod",
            "last_name": "Created",
            "password": STAFF_PASSWORD,
            "role_id": role_id,
        },
        headers=moderator_headers,
    )
    assert create_res.status_code == 403, create_res.text

    update_res = await client.patch(
        f"{API}/admin/staff/{target['id']}",
        json={"status": "blocked"},
        headers=moderator_headers,
    )
    assert update_res.status_code == 403, update_res.text

    matrix_res = await client.put(
        f"{API}/admin/roles/{role_id}/matrix",
        json={"permission_ids": []},
        headers=moderator_headers,
    )
    assert matrix_res.status_code == 403, matrix_res.text
