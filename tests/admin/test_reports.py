import pytest

from tests.sql import (
    fetchrow,
    insert_catalog_item_report,
    insert_proposal_report,
    insert_rfq_report,
)

API = "/api/v1"


async def _hide_active_catalog_items(client, admin_auth) -> None:
    list_res = await client.get(
        f"{API}/admin/catalog",
        params={"view": "all", "page_size": 100},
        headers=admin_auth["headers"],
    )
    if list_res.status_code != 200:
        return
    for item in list_res.json().get("items", []):
        if item.get("status") != "active":
            continue
        await client.post(
            f"{API}/admin/catalog/{item['id']}/action",
            json={"action": "hide", "reason": "Free catalog slots for reports tests"},
            headers=admin_auth["headers"],
        )


async def _create_pending_item(client, supplier_auth, admin_auth=None) -> int:
    if admin_auth:
        await _hide_active_catalog_items(client, admin_auth)
    categories = await client.get(f"{API}/public/categories")
    assert categories.status_code == 200
    category_id = categories.json()[0]["id"]
    create_res = await client.post(
        f"{API}/supplier/catalog/items",
        headers=supplier_auth["headers"],
        json={
            "type": "service",
            "category_id": category_id,
            "title": "Reports inbox catalog item",
            "description": "Описание для жалобы",
            "status": "pending_review",
            "attributes": [],
            "media": [
                {
                    "file_name": "cover.jpg",
                    "file_url": "https://example.com/cover.jpg",
                    "media_type": "image",
                }
            ],
            "pricing": {
                "pricing_type": "fixed",
                "currency": "TJS",
                "fixed_price": 1500,
                "hourly_rate": None,
                "monthly_rate": None,
                "tiers": [],
            },
        },
    )
    assert create_res.status_code == 200, create_res.text
    return create_res.json()["id"]


async def _approve_item(client, admin_auth, item_id: int) -> None:
    res = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_catalog_report_accepts_new_reasons(
    client, admin_auth, supplier_auth, buyer_auth
):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth, admin_auth)
    await _approve_item(client, admin_auth, item_id)

    report_res = await client.post(
        f"{API}/catalog/items/{item_id}/reports",
        json={"reason": "fraud", "details": "Подозрение на мошенничество"},
        headers=buyer_auth["headers"],
    )
    assert report_res.status_code == 200, report_res.text
    assert report_res.json()["reason"] == "fraud"


@pytest.mark.asyncio
async def test_admin_list_and_detail_reports(
    client, admin_auth, supplier_auth, buyer_auth
):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth, admin_auth)
    await _approve_item(client, admin_auth, item_id)

    report_res = await client.post(
        f"{API}/catalog/items/{item_id}/reports",
        json={"reason": "spam", "details": "Рекламный спам в описании"},
        headers=buyer_auth["headers"],
    )
    assert report_res.status_code == 200, report_res.text
    report_id = report_res.json()["id"]

    list_res = await client.get(
        f"{API}/admin/reports",
        params={"view": "spam", "query": "Reports inbox", "page_size": 10},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    data = list_res.json()
    assert data["view_counts"]["spam"] >= 1
    assert any(
        item["id"] == report_id and item["target_type"] == "catalog"
        for item in data["items"]
    )

    detail_res = await client.get(
        f"{API}/admin/reports/catalog/{report_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["reason"] == "spam"
    assert detail["reporter"]["email"]
    assert detail["reported_object"]["id"] == str(item_id)
    assert detail["evidence"]["details"] == "Рекламный спам в описании"
    assert isinstance(detail["evidence"]["files"], list)
    assert isinstance(detail["history"], list)


@pytest.mark.asyncio
async def test_admin_report_dismiss_and_warn(
    client, admin_auth, supplier_auth, buyer_auth
):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth, admin_auth)
    await _approve_item(client, admin_auth, item_id)

    report_res = await client.post(
        f"{API}/catalog/items/{item_id}/reports",
        json={"reason": "abuse", "details": "Оскорбительный контент"},
        headers=buyer_auth["headers"],
    )
    assert report_res.status_code == 200, report_res.text
    report_id = report_res.json()["id"]

    dismiss_res = await client.post(
        f"{API}/admin/reports/catalog/{report_id}/action",
        json={"action": "dismiss"},
        headers=admin_auth["headers"],
    )
    assert dismiss_res.status_code == 200, dismiss_res.text
    assert dismiss_res.json()["status"] == "dismissed"

    report = await fetchrow(
        """
        SELECT status::text AS status, reason::text AS reason
        FROM catalog_item_reports WHERE id = $1
        """,
        report_id,
    )
    assert report["status"] == "dismissed"
    assert report["reason"] == "abuse"

    item_id_2 = await _create_pending_item(client, supplier_auth, admin_auth)
    # Rename via second create - title collision ok
    await _approve_item(client, admin_auth, item_id_2)
    report_res_2 = await client.post(
        f"{API}/catalog/items/{item_id_2}/reports",
        json={"reason": "other", "details": "Прочее нарушение"},
        headers=buyer_auth["headers"],
    )
    assert report_res_2.status_code == 200, report_res_2.text
    report_id_2 = report_res_2.json()["id"]

    warn_res = await client.post(
        f"{API}/admin/reports/catalog/{report_id_2}/action",
        json={"action": "warn", "reason": "Предупреждение за жалобу"},
        headers=admin_auth["headers"],
    )
    assert warn_res.status_code == 200, warn_res.text
    assert warn_res.json()["status"] == "resolved"

    report = await fetchrow(
        "SELECT status::text AS status FROM catalog_item_reports WHERE id = $1",
        report_id_2,
    )
    assert report["status"] == "resolved"
    notification = await fetchrow(
        """
        SELECT title FROM notifications
        WHERE href = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        f"/supplier/catalog/{item_id_2}",
    )
    assert notification is not None
    assert "Предупреждение" in notification["title"]


@pytest.mark.asyncio
async def test_admin_report_suspend_blocks_owner(
    client, admin_auth, supplier_auth, buyer_auth
):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth, admin_auth)
    await _approve_item(client, admin_auth, item_id)

    report_res = await client.post(
        f"{API}/catalog/items/{item_id}/reports",
        json={"reason": "counterfeit", "details": "Подделка бренда"},
        headers=buyer_auth["headers"],
    )
    assert report_res.status_code == 200, report_res.text
    report_id = report_res.json()["id"]

    suspend_res = await client.post(
        f"{API}/admin/reports/catalog/{report_id}/action",
        json={"action": "suspend", "reason": "Повторные нарушения"},
        headers=admin_auth["headers"],
    )
    assert suspend_res.status_code == 200, suspend_res.text
    assert suspend_res.json()["status"] == "resolved"

    report = await fetchrow(
        "SELECT status::text AS status FROM catalog_item_reports WHERE id = $1",
        report_id,
    )
    assert report["status"] == "resolved"

    # Restore seeded supplier for subsequent tests sharing the account
    detail_res = await client.get(
        f"{API}/admin/reports/catalog/{report_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    owner = detail_res.json().get("owner") or {}
    company_id = owner.get("company_id")
    if company_id:
        restore = await client.post(
            f"{API}/admin/companies/{company_id}/action",
            json={"action": "reactivate", "reason": "Restore after suspend test"},
            headers=admin_auth["headers"],
        )
        assert restore.status_code == 200, restore.text
    if owner.get("user_id"):
        restore = await client.patch(
            f"{API}/admin/users/{owner['user_id']}/status",
            json={"status": "active"},
            headers=admin_auth["headers"],
        )
        assert restore.status_code == 200, restore.text


@pytest.mark.asyncio
async def test_admin_report_delete_removes_object(
    client, admin_auth, supplier_auth, buyer_auth
):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth, admin_auth)

    report_id = await insert_catalog_item_report(
        item_id=item_id,
        reporter_user_id=buyer_auth["me"]["user"]["id"],
        reason="fraud",
        details="Явный фрод",
    )

    delete_res = await client.post(
        f"{API}/admin/reports/catalog/{report_id}/action",
        json={"action": "delete", "reason": "Удаление по жалобе"},
        headers=admin_auth["headers"],
    )
    assert delete_res.status_code == 200, delete_res.text
    assert delete_res.json()["status"] == "resolved"

    public_res = await client.get(f"{API}/public/catalog/items/{item_id}")
    assert public_res.status_code == 404


@pytest.mark.asyncio
async def test_admin_reports_include_rfq_and_proposal(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    rfq_res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": "Reports RFQ target",
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "draft",
            "project_duration": "1 month",
            "start_date": "2026-09-01",
        },
    )
    assert rfq_res.status_code == 200, rfq_res.text
    rfq_id = rfq_res.json()["id"]

    await insert_rfq_report(
        rfq_id=rfq_id,
        reporter_user_id=supplier_auth["me"]["user"]["id"],
        reason="spam",
        details="RFQ spam report",
    )

    publish = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    assert publish.status_code == 200, publish.text

    proposal_res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={
            "price": 9000,
            "currency": "TJS",
            "delivery_time": "7 days",
            "message": "Reports proposal target",
        },
    )
    assert proposal_res.status_code == 200, proposal_res.text
    proposal_id = proposal_res.json()["id"]

    await insert_proposal_report(
        proposal_id=proposal_id,
        reporter_user_id=buyer_auth["me"]["user"]["id"],
        reason="abuse",
        details="Proposal abuse report",
    )

    list_res = await client.get(
        f"{API}/admin/reports",
        params={"view": "all", "page_size": 50},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    items = list_res.json()["items"]
    assert any(
        item["target_type"] == "rfq" and item["reported_object"]["id"] == rfq_id
        for item in items
    )
    assert any(
        item["target_type"] == "proposal"
        and item["reported_object"]["id"] == str(proposal_id)
        for item in items
    )
