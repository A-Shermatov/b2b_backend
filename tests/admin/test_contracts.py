import pytest

from tests.sql import execute, fetch, upsert_moderator

API = "/api/v1"


async def _create_draft_rfq(client, buyer_auth, title: str = "Admin contract RFQ") -> str:
    res = await client.post(
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
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def _create_contract(client, buyer_auth, supplier_auth, title: str) -> int:
    rfq_id = await _create_draft_rfq(client, buyer_auth, title)
    publish_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    assert publish_res.status_code == 200, publish_res.text

    proposal_res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={
            "price": 15000,
            "currency": "TJS",
            "delivery_time": "14 days",
            "message": "Готовы выполнить контракт",
        },
    )
    assert proposal_res.status_code == 200, proposal_res.text
    proposal_id = proposal_res.json()["id"]

    accept_res = await client.post(
        f"{API}/buyer/proposals/{proposal_id}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": "full_prepayment"},
    )
    assert accept_res.status_code == 200, accept_res.text
    return accept_res.json()["contract_id"]


@pytest.mark.asyncio
async def test_admin_list_contracts_filters_and_detail(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    contract_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Active contract list"
    )

    list_res = await client.get(
        f"{API}/admin/contracts",
        params={"view": "active", "query": "Active contract", "page_size": 10},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    data = list_res.json()
    assert data["view_counts"]["active"] >= 1
    assert any(item["id"] == contract_id for item in data["items"])

    detail_res = await client.get(
        f"{API}/admin/contracts/{contract_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["title"] == "Active contract list"
    assert detail["status"] == "pending_payment"
    assert isinstance(detail["milestones"], list)
    assert isinstance(detail["files"], list)
    assert isinstance(detail["messages"], list)
    assert isinstance(detail["history"], list)
    assert "held" in detail["escrow"]
    assert detail["payment_plan"] is not None


@pytest.mark.asyncio
async def test_admin_contract_actions(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    freeze_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Freeze contract"
    )
    cancel_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Cancel contract"
    )
    force_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Force complete contract"
    )
    investigate_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Investigate contract"
    )

    for contract_id in (freeze_id, force_id):
        await execute(
            "UPDATE contracts SET status = 'active'::contract_status WHERE id = $1",
            contract_id,
        )
        await execute(
            """
            UPDATE payment_milestones
            SET status = 'funded'::milestone_status
            WHERE contract_id = $1
            """,
            contract_id,
        )

    freeze_res = await client.post(
        f"{API}/admin/contracts/{freeze_id}/action",
        json={"action": "freeze", "reason": "Подозрение на мошенничество"},
        headers=admin_auth["headers"],
    )
    assert freeze_res.status_code == 200, freeze_res.text
    assert freeze_res.json()["status"] == "active"

    milestones = await fetch(
        "SELECT status::text AS status FROM payment_milestones WHERE contract_id = $1",
        freeze_id,
    )
    assert milestones
    assert all(m["status"] == "disputed" for m in milestones)

    cancel_res = await client.post(
        f"{API}/admin/contracts/{cancel_id}/action",
        json={"action": "cancel", "reason": "Нарушение условий"},
        headers=admin_auth["headers"],
    )
    assert cancel_res.status_code == 200, cancel_res.text
    assert cancel_res.json()["status"] == "cancelled"

    force_res = await client.post(
        f"{API}/admin/contracts/{force_id}/action",
        json={"action": "force_complete", "reason": "Стороны согласовали завершение"},
        headers=admin_auth["headers"],
    )
    assert force_res.status_code == 200, force_res.text
    assert force_res.json()["status"] == "completed"

    milestones = await fetch(
        "SELECT status::text AS status FROM payment_milestones WHERE contract_id = $1",
        force_id,
    )
    assert milestones
    assert all(m["status"] == "released" for m in milestones)

    investigate_res = await client.post(
        f"{API}/admin/contracts/{investigate_id}/action",
        json={"action": "open_investigation", "reason": "Открыть спор"},
        headers=admin_auth["headers"],
    )
    assert investigate_res.status_code == 200, investigate_res.text
    assert investigate_res.json()["status"] == "disputed"

    list_cancelled = await client.get(
        f"{API}/admin/contracts",
        params={"view": "cancelled"},
        headers=admin_auth["headers"],
    )
    assert any(item["id"] == cancel_id for item in list_cancelled.json()["items"])

    list_completed = await client.get(
        f"{API}/admin/contracts",
        params={"view": "completed"},
        headers=admin_auth["headers"],
    )
    assert any(item["id"] == force_id for item in list_completed.json()["items"])

    list_disputed = await client.get(
        f"{API}/admin/contracts",
        params={"view": "disputed"},
        headers=admin_auth["headers"],
    )
    assert any(item["id"] == investigate_id for item in list_disputed.json()["items"])

    audits = await fetch(
        """
        SELECT id FROM audit_logs
        WHERE resource_type = 'contract' AND action LIKE 'admin.contract.%'
        """
    )
    assert len(audits) >= 4
    notifications = await fetch(
        """
        SELECT id FROM notifications
        WHERE title = ANY($1::text[])
        """,
        [
            "Escrow заморожен",
            "Контракт отменён",
            "Контракт принудительно завершён",
            "Открыто расследование",
        ],
    )
    assert len(notifications) >= 4


@pytest.mark.asyncio
async def test_buyer_cannot_access_admin_contracts(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    res = await client.get(
        f"{API}/admin/contracts",
        headers=buyer_auth["headers"],
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_moderator_cannot_force_complete(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    contract_id = await _create_contract(
        client, buyer_auth, supplier_auth, "Moderator force complete"
    )

    await upsert_moderator(first_name="Mod", last_name="Erator")

    login_res = await client.post(
        f"{API}/auth/login",
        json={"email": "moderator@example.com", "password": "Moderator123!"},
    )
    assert login_res.status_code == 200, login_res.text
    token = login_res.json()["access_token"]
    mod_headers = {"Authorization": f"Bearer {token}"}

    force_res = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        json={"action": "force_complete", "reason": "Нельзя"},
        headers=mod_headers,
    )
    assert force_res.status_code == 403

    cancel_without_reason = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        json={"action": "cancel"},
        headers=admin_auth["headers"],
    )
    assert cancel_without_reason.status_code == 422
