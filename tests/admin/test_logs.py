import pytest

from app.modules.admin.services.logs_catalog import (
    action_kind,
    object_id_for,
    section_for,
    status_group,
)
from tests.staff import create_role_with_permissions, create_staff, login_headers, unique_suffix

API = "/api/v1"


@pytest.mark.parametrize(
    ("code", "group"),
    [
        (200, "success"),
        (201, "success"),
        (204, "success"),
        (400, "invalid"),
        (422, "invalid"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (429, "rate_limited"),
        (418, "rejected"),
        (500, "server_error"),
        (503, "server_error"),
    ],
)
def test_status_group(code, group):
    assert status_group(code) == group


@pytest.mark.parametrize(
    ("method", "path", "kind", "section"),
    [
        ("POST", "/api/v1/auth/login", "login", "auth"),
        ("POST", "/api/v1/auth/register", "register", "auth"),
        ("POST", "/api/v1/auth/switch-company", "update", "auth"),
        ("POST", "/api/v1/buyer/rfqs/", "create", "rfqs"),
        ("POST", "/api/v1/buyer/rfqs/abc/publish", "update", "rfqs"),
        ("POST", "/api/v1/buyer/contracts/5/messages", "create", "contracts"),
        ("POST", "/api/v1/admin/disputes/7/action", "update", "disputes"),
        ("PATCH", "/api/v1/admin/staff/3", "update", "staff"),
        ("PUT", "/api/v1/admin/roles/3/matrix", "update", "roles"),
        ("DELETE", "/api/v1/admin/categories/9", "delete", "catalog"),
        ("GET", "/api/v1/admin/companies", "view", "companies"),
        ("GET", "/api/v1/catalog/items/4", "view", "catalog"),
        ("POST", "/api/v1/admin/something/else", "create", "other"),
    ],
)
def test_action_kind_and_section(method, path, kind, section):
    assert action_kind(method, path) == kind
    assert section_for(path) == section


def test_object_id():
    assert object_id_for("/api/v1/admin/staff/4") == "4"
    assert object_id_for("/api/v1/buyer/contracts/12/messages") == "12"
    assert object_id_for("/api/v1/auth/switch-company") is None


async def _logs(client, headers, **params):
    res = await client.get(f"{API}/admin/logs", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_logs_split_users_and_staff(client, admin_auth, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    title = f"Log split {unique_suffix()}"
    created = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": title,
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "draft",
            "project_duration": "2 months",
            "start_date": "2026-07-01",
        },
    )
    assert created.status_code == 200, created.text

    role_name = f"log-role-{unique_suffix()}"
    await create_role_with_permissions(client, admin_auth["headers"], role_name, [])

    users = await _logs(
        client,
        admin_auth["headers"],
        audience="users",
        query="buyer@example.com",
        section="rfqs",
        kind="create",
        status="success",
    )
    assert users["items"], users
    item = users["items"][0]
    assert item["audience"] == "users"
    assert item["user"]["email"] == "buyer@example.com"
    assert (item["kind"], item["section"], item["status"]) == ("create", "rfqs", "success")
    assert "status_code" not in item
    assert item["path"] == f"{API}/buyer/rfqs/"
    assert users["audience_counts"]["staff"] == 0

    staff = await _logs(
        client,
        admin_auth["headers"],
        audience="staff",
        query="admin@example.com",
        section="roles",
        kind="create",
    )
    assert staff["items"], staff
    assert all(entry["audience"] == "staff" for entry in staff["items"])
    assert staff["audience_counts"]["users"] == 0

    detail = await client.get(
        f"{API}/admin/logs/{staff['items'][0]['id']}", headers=admin_auth["headers"]
    )
    assert detail.status_code == 200, detail.text
    fields = {field["key"]: field for field in detail.json()["fields"]}
    assert fields["name"]["value"] == role_name
    assert "status_code" not in detail.json()


@pytest.mark.asyncio
async def test_failed_guest_login_is_visible_without_codes(client, admin_auth):
    email = f"ghost-{unique_suffix()}@example.com"
    failed = await client.post(
        f"{API}/auth/login", json={"email": email, "password": "WrongPass123!"}
    )
    assert failed.status_code == 401, failed.text

    logs = await _logs(
        client,
        admin_auth["headers"],
        audience="users",
        status="unauthorized",
        kind="login",
        query=email,
    )
    assert len(logs["items"]) == 1
    item = logs["items"][0]
    assert item["user"] is None
    assert item["guest_email"] == email
    assert item["status"] == "unauthorized"

    detail = await client.get(f"{API}/admin/logs/{item['id']}", headers=admin_auth["headers"])
    fields = {field["key"]: field for field in detail.json()["fields"]}
    assert fields["password"]["hidden"] is True
    assert fields["password"]["value"] == ""


@pytest.mark.asyncio
async def test_logs_require_permission(client, admin_auth):
    role_id = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"no-logs-{unique_suffix()}",
        ["admin_disputes.view"],
    )
    staff = await create_staff(client, admin_auth["headers"], role_id)
    headers = await login_headers(client, staff["email"])

    res = await client.get(f"{API}/admin/logs", headers=headers)
    assert res.status_code == 403
