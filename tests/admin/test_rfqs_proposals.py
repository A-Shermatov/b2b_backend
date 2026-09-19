import pytest

from tests.sql import (
    fetch,
    fetchrow,
    fetchval,
    insert_proposal_report,
    insert_rfq_report,
    upsert_moderator,
    user_id_by_email,
)

API = "/api/v1"


async def _create_draft_rfq(client, buyer_auth, title: str = "Admin RFQ test") -> str:
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


@pytest.mark.asyncio
async def test_admin_list_and_view_rfq(client, admin_auth, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    rfq_id = await _create_draft_rfq(client, buyer_auth, "Draft list RFQ")

    list_res = await client.get(
        f"{API}/admin/rfqs",
        params={"view": "draft", "query": "Draft list", "page_size": 10},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    data = list_res.json()
    assert data["view_counts"]["draft"] >= 1
    assert any(item["id"] == rfq_id for item in data["items"])

    detail_res = await client.get(
        f"{API}/admin/rfqs/{rfq_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["title"] == "Draft list RFQ"
    assert isinstance(detail["requirements"], dict)
    assert isinstance(detail["proposals"], list)
    assert isinstance(detail["messages"], list)
    assert isinstance(detail["reports"], list)
    assert isinstance(detail["history"], list)


@pytest.mark.asyncio
async def test_admin_rfq_actions_and_reports(client, admin_auth, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    rfq_id = await _create_draft_rfq(client, buyer_auth, "Published RFQ action")

    publish_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    assert publish_res.status_code == 200, publish_res.text

    buyer_id = await user_id_by_email("buyer@example.com")
    await insert_rfq_report(
        rfq_id=rfq_id,
        reporter_user_id=buyer_id,
        reason="spam",
        details="Spam RFQ",
    )

    reported_res = await client.get(
        f"{API}/admin/rfqs",
        params={"view": "reported"},
        headers=admin_auth["headers"],
    )
    assert reported_res.status_code == 200
    assert any(item["id"] == rfq_id for item in reported_res.json()["items"])

    close_res = await client.post(
        f"{API}/admin/rfqs/{rfq_id}/action",
        json={"action": "close", "reason": "Срок вышел"},
        headers=admin_auth["headers"],
    )
    assert close_res.status_code == 200, close_res.text
    assert close_res.json()["status"] == "cancelled"

    warn_res = await client.post(
        f"{API}/admin/rfqs/{rfq_id}/action",
        json={"action": "warn_buyer", "reason": "Нарушение правил"},
        headers=admin_auth["headers"],
    )
    assert warn_res.status_code == 200, warn_res.text

    hide_res = await client.post(
        f"{API}/admin/rfqs/{rfq_id}/action",
        json={"action": "hide", "reason": "Жалоба подтверждена"},
        headers=admin_auth["headers"],
    )
    assert hide_res.status_code == 200, hide_res.text
    assert hide_res.json()["status"] == "archived"

    rfq_status = await fetchval(
        "SELECT status::text FROM rfqs WHERE id = $1",
        rfq_id,
    )
    assert rfq_status == "archived"
    reports = await fetch(
        "SELECT status::text AS status FROM rfq_reports WHERE rfq_id = $1",
        rfq_id,
    )
    assert reports
    assert all(report["status"] != "open" for report in reports)
    notification = await fetchrow(
        """
        SELECT id FROM notifications
        WHERE href = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        f"/customer/rfqs/{rfq_id}",
    )
    assert notification is not None
    audit = await fetchrow(
        """
        SELECT action FROM audit_logs
        WHERE resource_type = 'rfq' AND resource_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        rfq_id,
    )
    assert audit is not None
    assert audit["action"] == "admin.rfq.hide"


@pytest.mark.asyncio
async def test_admin_delete_draft_rfq(client, admin_auth, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    rfq_id = await _create_draft_rfq(client, buyer_auth, "Delete draft RFQ")

    delete_res = await client.post(
        f"{API}/admin/rfqs/{rfq_id}/action",
        json={"action": "delete", "reason": "Тестовое удаление"},
        headers=admin_auth["headers"],
    )
    assert delete_res.status_code == 200, delete_res.text
    assert delete_res.json()["status"] == "deleted"

    detail_res = await client.get(
        f"{API}/admin/rfqs/{rfq_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 404


@pytest.mark.asyncio
async def test_buyer_cannot_access_admin_rfqs(client, buyer_auth):
    res = await client.get(f"{API}/admin/rfqs", headers=buyer_auth["headers"])
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_proposals_list_detail_and_actions(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    rfq_id = await _create_draft_rfq(client, buyer_auth, "Proposal RFQ")
    publish_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    assert publish_res.status_code == 200, publish_res.text

    proposal_res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={
            "price": 12000,
            "currency": "TJS",
            "delivery_time": "10 days",
            "message": "Готовы выполнить заказ качественно и в срок",
        },
    )
    assert proposal_res.status_code == 200, proposal_res.text
    proposal_id = proposal_res.json()["id"]

    buyer_id = await user_id_by_email("buyer@example.com")
    await insert_proposal_report(
        proposal_id=proposal_id,
        reporter_user_id=buyer_id,
        reason="abuse",
        details="Подозрительная цена",
    )

    list_res = await client.get(
        f"{API}/admin/proposals",
        params={"view": "pending", "query": str(proposal_id)},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    assert any(item["id"] == proposal_id for item in list_res.json()["items"])

    reported_res = await client.get(
        f"{API}/admin/proposals",
        params={"view": "reported"},
        headers=admin_auth["headers"],
    )
    assert reported_res.status_code == 200
    assert any(item["id"] == proposal_id for item in reported_res.json()["items"])

    detail_res = await client.get(
        f"{API}/admin/proposals/{proposal_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["price"] == 12000
    assert detail["message"]
    assert isinstance(detail["reports"], list)
    assert isinstance(detail["messages"], list)

    investigate_res = await client.post(
        f"{API}/admin/proposals/{proposal_id}/action",
        json={"action": "investigate", "reason": "Проверили жалобу"},
        headers=admin_auth["headers"],
    )
    assert investigate_res.status_code == 200, investigate_res.text

    reports = await fetch(
        "SELECT status::text AS status FROM proposal_reports WHERE proposal_id = $1",
        proposal_id,
    )
    assert reports
    assert all(report["status"] != "open" for report in reports)

    delete_res = await client.post(
        f"{API}/admin/proposals/{proposal_id}/action",
        json={"action": "delete", "reason": "Удаление тестового предложения"},
        headers=admin_auth["headers"],
    )
    assert delete_res.status_code == 200, delete_res.text
    assert delete_res.json()["status"] == "deleted"

    detail_after = await client.get(
        f"{API}/admin/proposals/{proposal_id}",
        headers=admin_auth["headers"],
    )
    assert detail_after.status_code == 404


@pytest.mark.asyncio
async def test_moderator_cannot_delete_proposal(client, admin_auth, buyer_auth, supplier_auth):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    rfq_id = await _create_draft_rfq(client, buyer_auth, "Moderator proposal RFQ")
    await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    proposal_res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={
            "price": 5000,
            "currency": "TJS",
            "delivery_time": "5 days",
            "message": "Модератор не должен удалять это предложение",
        },
    )
    assert proposal_res.status_code == 200, proposal_res.text
    proposal_id = proposal_res.json()["id"]

    await upsert_moderator()

    login_res = await client.post(
        f"{API}/auth/login",
        json={"email": "moderator@example.com", "password": "Moderator123!"},
    )
    assert login_res.status_code == 200, login_res.text
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    delete_res = await client.post(
        f"{API}/admin/proposals/{proposal_id}/action",
        json={"action": "delete", "reason": "Попытка удаления"},
        headers=headers,
    )
    assert delete_res.status_code == 403

    proposal_status = await fetchval(
        "SELECT status::text FROM proposals WHERE id = $1",
        proposal_id,
    )
    assert proposal_status == "submitted"
