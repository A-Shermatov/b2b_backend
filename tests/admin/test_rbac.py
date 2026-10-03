import pytest

from tests.admin.test_disputes import _create_contract, _open_dispute
from tests.sql import fetch, fetchrow, fetchval, upsert_moderator
from tests.staff import create_role_with_permissions, create_staff, login_headers, unique_suffix


@pytest.mark.asyncio
async def test_admin_can_list_users(client, admin_auth):
    res = await client.get("/api/v1/admin/users", headers=admin_auth["headers"])
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {
        "items",
        "total",
        "page",
        "page_size",
        "pages",
        "status_counts",
    }
    assert isinstance(data["items"], list)
    assert data["total"] >= len(data["items"])
    assert data["status_counts"]["all"] >= data["total"]


@pytest.mark.asyncio
async def test_admin_can_search_filter_and_paginate_users(client, admin_auth):
    search_res = await client.get(
        "/api/v1/admin/users",
        params={"query": "buyer@example.com", "status": "active", "page_size": 1},
        headers=admin_auth["headers"],
    )

    assert search_res.status_code == 200
    data = search_res.json()
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert all(user["status"] == "active" for user in data["items"])
    assert all("buyer@example.com" in user["email"] for user in data["items"])


@pytest.mark.asyncio
async def test_admin_can_block_and_unblock_regular_user(client, admin_auth):
    users_res = await client.get(
        "/api/v1/admin/users",
        params={"query": "buyer@example.com"},
        headers=admin_auth["headers"],
    )
    user_id = users_res.json()["items"][0]["id"]

    blocked_res = await client.patch(
        f"/api/v1/admin/users/{user_id}/status",
        json={"status": "blocked"},
        headers=admin_auth["headers"],
    )
    assert blocked_res.status_code == 200
    assert blocked_res.json()["status"] == "blocked"

    active_res = await client.patch(
        f"/api/v1/admin/users/{user_id}/status",
        json={"status": "active"},
        headers=admin_auth["headers"],
    )
    assert active_res.status_code == 200
    assert active_res.json()["status"] == "active"


@pytest.mark.asyncio
async def test_admin_cannot_change_own_status(client, admin_auth):
    me_res = await client.get("/api/v1/auth/me", headers=admin_auth["headers"])
    admin_id = me_res.json()["user"]["id"]

    res = await client.patch(
        f"/api/v1/admin/users/{admin_id}/status",
        json={"status": "blocked"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_view_dashboard(client, admin_auth):
    res = await client.get("/api/v1/admin/dashboard", headers=admin_auth["headers"])

    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"metrics", "recent_activity"}
    assert data["metrics"]["total_users"] >= 1
    assert data["metrics"]["total_companies"] >= 0
    assert data["metrics"]["escrow_balance"] >= 0
    assert data["metrics"]["monthly_revenue"] >= 0
    assert isinstance(data["recent_activity"], list)
    assert all(
        item["type"] in {"registration", "contract", "payment", "dispute"}
        for item in data["recent_activity"]
    )


@pytest.mark.asyncio
async def test_admin_can_list_search_and_view_company_details(client, admin_auth):
    list_res = await client.get(
        "/api/v1/admin/companies",
        params={"query": "ТехноСнаб", "status": "verified", "page_size": 1},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    data = list_res.json()
    assert data["page_size"] == 1
    assert data["status_counts"]["all"] >= data["total"]
    assert data["items"]
    assert data["items"][0]["title"] == "ТехноСнаб"

    company_id = data["items"][0]["id"]
    detail_res = await client.get(
        f"/api/v1/admin/companies/{company_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["owner"]["email"] == "supplier@example.com"
    assert isinstance(detail["members"], list)
    assert isinstance(detail["products"], list)
    assert isinstance(detail["services"], list)
    assert set(detail["verification_checklist"]) == {
        "legal_name",
        "tax_number",
        "address",
        "website",
        "certificates",
    }


@pytest.mark.asyncio
async def test_admin_company_actions_update_actors_notifications_and_audit(client, admin_auth):
    companies_res = await client.get(
        "/api/v1/admin/companies",
        params={"query": "supplier@example.com"},
        headers=admin_auth["headers"],
    )
    company_id = companies_res.json()["items"][0]["id"]

    documents_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "request_documents", "reason": "Добавьте выписку"},
        headers=admin_auth["headers"],
    )
    assert documents_res.status_code == 200, documents_res.text
    assert documents_res.json()["verification_status"] == "needs_documents"

    block_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "block", "reason": "Проверка безопасности"},
        headers=admin_auth["headers"],
    )
    assert block_res.status_code == 200, block_res.text
    assert block_res.json()["operational_status"] == "blocked"

    actors = await fetch(
        "SELECT is_active FROM actors WHERE company_id = $1",
        company_id,
    )
    assert actors
    assert all(not actor["is_active"] for actor in actors)
    notification = await fetchrow(
        """
        SELECT id FROM notifications
        WHERE company_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        company_id,
    )
    assert notification is not None
    audit = await fetchrow(
        """
        SELECT action FROM audit_logs
        WHERE resource_type = 'company' AND resource_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        str(company_id),
    )
    assert audit is not None
    assert audit["action"] == "admin.company.block"

    reactivate_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "reactivate"},
        headers=admin_auth["headers"],
    )
    assert reactivate_res.status_code == 200, reactivate_res.text
    assert reactivate_res.json()["operational_status"] == "active"

    approve_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert approve_res.status_code == 200, approve_res.text


@pytest.mark.asyncio
async def test_company_negative_actions_require_reason(client, admin_auth):
    companies_res = await client.get(
        "/api/v1/admin/companies",
        headers=admin_auth["headers"],
    )
    company_id = companies_res.json()["items"][0]["id"]
    res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "deactivate"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_moderator_can_verify_but_cannot_block_company(client, admin_auth):
    await upsert_moderator()

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "moderator@example.com", "password": "Moderator123!"},
    )
    assert login_res.status_code == 200, login_res.text
    moderator_headers = {
        "Authorization": f"Bearer {login_res.json()['access_token']}",
    }
    companies_res = await client.get(
        "/api/v1/admin/companies",
        headers=moderator_headers,
    )
    assert companies_res.status_code == 200
    company_id = companies_res.json()["items"][0]["id"]

    request_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "request_documents", "reason": "Нужна выписка"},
        headers=moderator_headers,
    )
    assert request_res.status_code == 200, request_res.text

    block_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "block", "reason": "Недоступное действие"},
        headers=moderator_headers,
    )
    assert block_res.status_code == 403

    restore_res = await client.post(
        f"/api/v1/admin/companies/{company_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert restore_res.status_code == 200


@pytest.mark.asyncio
async def test_dispute_only_role_is_limited_to_disputes(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    role_id = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"disputes-{unique_suffix()}",
        ["admin_disputes.view", "admin_disputes.update"],
    )
    staff = await create_staff(client, admin_auth["headers"], role_id)
    assert staff["platform_role"] == "moderator"
    headers = await login_headers(client, staff["email"])

    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200, me_res.text
    me = me_res.json()
    assert me["permissions"] == ["admin_disputes.update", "admin_disputes.view"]
    assert me["staff_role"]["id"] == role_id

    contract_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Dispute-only moderator"
    )
    await _open_dispute(client, buyer_auth, contract_id, "Проверка доступа модератора споров")
    dispute_id = await fetchval("SELECT id FROM disputes WHERE contract_id = $1", contract_id)

    list_res = await client.get("/api/v1/admin/disputes", headers=headers)
    assert list_res.status_code == 200, list_res.text
    detail_res = await client.get(f"/api/v1/admin/disputes/{dispute_id}", headers=headers)
    assert detail_res.status_code == 200, detail_res.text
    action_res = await client.post(
        f"/api/v1/admin/disputes/{dispute_id}/action",
        json={"action": "request_evidence", "reason": "Приложите акты"},
        headers=headers,
    )
    assert action_res.status_code == 200, action_res.text

    for path in (
        "/api/v1/admin/dashboard",
        "/api/v1/admin/users",
        "/api/v1/admin/companies",
        "/api/v1/admin/finance",
        "/api/v1/admin/contracts",
        "/api/v1/admin/staff",
        "/api/v1/admin/roles",
    ):
        denied = await client.get(path, headers=headers)
        assert denied.status_code == 403, f"{path}: {denied.status_code}"

    company_action = await client.post(
        "/api/v1/admin/companies/1/action",
        json={"action": "approve"},
        headers=headers,
    )
    assert company_action.status_code == 403


@pytest.mark.asyncio
async def test_blocked_staff_loses_access(client, admin_auth):
    role_id = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"blocked-{unique_suffix()}",
        ["admin_disputes.view"],
    )
    staff = await create_staff(client, admin_auth["headers"], role_id)
    headers = await login_headers(client, staff["email"])

    blocked = await client.patch(
        f"/api/v1/admin/staff/{staff['id']}",
        json={"status": "blocked"},
        headers=admin_auth["headers"],
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"

    res = await client.get("/api/v1/admin/disputes", headers=headers)
    assert res.status_code in (401, 403)


@pytest.mark.asyncio
async def test_buyer_cannot_access_admin(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer company")
    res = await client.get("/api/v1/admin/users", headers=buyer_auth["headers"])
    assert res.status_code == 403

    dashboard_res = await client.get(
        "/api/v1/admin/dashboard",
        headers=buyer_auth["headers"],
    )
    assert dashboard_res.status_code == 403

    company_action_res = await client.post(
        "/api/v1/admin/companies/1/action",
        json={"action": "approve"},
        headers=buyer_auth["headers"],
    )
    assert company_action_res.status_code == 403


@pytest.mark.asyncio
async def test_supplier_cannot_access_admin(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier company")
    res = await client.get("/api/v1/admin/users", headers=supplier_auth["headers"])
    assert res.status_code == 403
