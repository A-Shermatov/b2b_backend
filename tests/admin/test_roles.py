import pytest

from tests.sql import fetchval, user_id_by_email
from tests.staff import create_role_with_permissions, create_staff, permission_ids, unique_suffix

API = "/api/v1"


@pytest.mark.asyncio
async def test_roles_and_catalog_are_limited_to_staff_scope(client, admin_auth):
    headers = admin_auth["headers"]

    roles_res = await client.get(f"{API}/admin/roles", headers=headers)
    assert roles_res.status_code == 200, roles_res.text
    role_names = {role["name"] for role in roles_res.json()}
    assert {"admin", "moderator"} <= role_names
    assert not role_names & {"buyer", "supplier", "both", "superadmin"}

    catalog = await client.get(f"{API}/admin/permissions/matrix/catalog", headers=headers)
    assert catalog.status_code == 200, catalog.text
    resources = {row["resource"] for row in catalog.json()["rows"]}
    assert {"admin_disputes", "admin_staff", "roles"} <= resources
    assert not resources & {"rfqs", "proposals", "payments", "company_profile"}

    buyer_role_id = await fetchval("SELECT id FROM roles WHERE name = 'buyer'")
    buyer_matrix = await client.get(f"{API}/admin/roles/{buyer_role_id}/matrix", headers=headers)
    assert buyer_matrix.status_code == 404


@pytest.mark.asyncio
async def test_role_matrix_crud(client, admin_auth):
    headers = admin_auth["headers"]
    role_id = await create_role_with_permissions(
        client, headers, f"viewer-{unique_suffix()}", ["admin_rfqs.view"]
    )

    matrix = await client.get(f"{API}/admin/roles/{role_id}/matrix", headers=headers)
    assert matrix.status_code == 200, matrix.text
    rfq_row = next(row for row in matrix.json()["rows"] if row["resource"] == "admin_rfqs")
    assert rfq_row["read"]["enabled"] is True
    assert rfq_row["write"]["enabled"] is False

    write_only = await client.put(
        f"{API}/admin/roles/{role_id}/matrix",
        json={"permission_ids": await permission_ids("admin_contracts.update")},
        headers=headers,
    )
    assert write_only.status_code == 200, write_only.text
    rows = {row["resource"]: row for row in write_only.json()["rows"]}
    assert rows["admin_contracts"]["read"]["enabled"] is True
    assert rows["admin_contracts"]["write"]["enabled"] is True
    assert rows["admin_rfqs"]["read"]["enabled"] is False

    marketplace = await client.put(
        f"{API}/admin/roles/{role_id}/matrix",
        json={"permission_ids": await permission_ids("rfqs.view")},
        headers=headers,
    )
    assert marketplace.status_code == 422, marketplace.text

    removed = await client.delete(f"{API}/admin/roles/{role_id}", headers=headers)
    assert removed.status_code == 204, removed.text


@pytest.mark.asyncio
async def test_role_in_use_and_system_roles_are_protected(client, admin_auth):
    headers = admin_auth["headers"]
    role_id = await create_role_with_permissions(
        client, headers, f"in-use-{unique_suffix()}", ["admin_disputes.view"]
    )
    await create_staff(client, headers, role_id)

    in_use = await client.delete(f"{API}/admin/roles/{role_id}", headers=headers)
    assert in_use.status_code == 409, in_use.text

    moderator_id = await fetchval("SELECT id FROM roles WHERE name = 'moderator'")
    system_delete = await client.delete(f"{API}/admin/roles/{moderator_id}", headers=headers)
    assert system_delete.status_code in (403, 409), system_delete.text

    reserved = await client.post(
        f"{API}/admin/roles",
        json={"name": "superadmin", "description": None},
        headers=headers,
    )
    assert reserved.status_code == 409, reserved.text


@pytest.mark.asyncio
async def test_admin_cannot_manage_admin_role(client, admin_auth):
    headers = admin_auth["headers"]
    admin_role_id = await fetchval("SELECT id FROM roles WHERE name = 'admin'")
    res = await client.put(
        f"{API}/admin/roles/{admin_role_id}/matrix",
        json={"permission_ids": await permission_ids("admin_disputes.view")},
        headers=headers,
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_staff_role_cannot_be_assigned_to_marketplace_user(client, admin_auth):
    headers = admin_auth["headers"]
    role_id = await create_role_with_permissions(
        client, headers, f"assign-{unique_suffix()}", ["admin_disputes.view"]
    )
    buyer_id = await user_id_by_email("buyer@example.com")
    role_before = await fetchval("SELECT role::text FROM users WHERE id = $1", buyer_id)

    assigned = await client.post(
        f"{API}/admin/roles/assign",
        json={"user_id": buyer_id, "role_id": role_id},
        headers=headers,
    )
    assert assigned.status_code == 404, assigned.text
    role_after = await fetchval("SELECT role::text FROM users WHERE id = $1", buyer_id)
    assert role_after == role_before
    assert role_after in {"buyer", "supplier", "both"}
