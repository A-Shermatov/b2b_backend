import pytest

from tests.sql import fetch, fetchrow, fetchval, upsert_moderator


async def _create_pending_item(client, supplier_auth) -> int:
    categories = await client.get("/api/v1/public/categories")
    assert categories.status_code == 200
    category_id = categories.json()[0]["id"]
    create_res = await client.post(
        "/api/v1/supplier/catalog/items",
        headers=supplier_auth["headers"],
        json={
            "type": "service",
            "category_id": category_id,
            "title": "Модерация тест услуги",
            "description": "Описание тестовой услуги для модерации",
            "status": "pending_review",
            "attributes": [{"name": "Срок", "value": "3 дня", "value_type": "text"}],
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


@pytest.mark.asyncio
async def test_admin_can_list_and_view_catalog_item(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth)

    list_res = await client.get(
        "/api/v1/admin/catalog",
        params={"view": "draft", "query": "Модерация тест", "page_size": 5},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    data = list_res.json()
    assert data["view_counts"]["all"] >= data["total"]
    assert any(item["id"] == item_id for item in data["items"])

    detail_res = await client.get(
        f"/api/v1/admin/catalog/{item_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["title"] == "Модерация тест услуги"
    assert isinstance(detail["media"], list)
    assert isinstance(detail["attributes"], list)
    assert isinstance(detail["reports"], list)
    assert isinstance(detail["history"], list)


@pytest.mark.asyncio
async def test_catalog_report_and_admin_actions(client, admin_auth, supplier_auth, buyer_auth):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_pending_item(client, supplier_auth)

    approve_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert approve_res.status_code == 200, approve_res.text
    assert approve_res.json()["status"] == "active"

    own_report = await client.post(
        f"/api/v1/catalog/items/{item_id}/reports",
        json={"reason": "spam", "details": "Своя позиция"},
        headers=supplier_auth["headers"],
    )
    assert own_report.status_code == 403

    report_res = await client.post(
        f"/api/v1/catalog/items/{item_id}/reports",
        json={"reason": "abuse", "details": "Цена не соответствует"},
        headers=buyer_auth["headers"],
    )
    assert report_res.status_code == 200, report_res.text

    duplicate_res = await client.post(
        f"/api/v1/catalog/items/{item_id}/reports",
        json={"reason": "spam"},
        headers=buyer_auth["headers"],
    )
    assert duplicate_res.status_code == 409

    reported_res = await client.get(
        "/api/v1/admin/catalog",
        params={"view": "reported"},
        headers=admin_auth["headers"],
    )
    assert reported_res.status_code == 200
    assert any(item["id"] == item_id for item in reported_res.json()["items"])

    hide_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "hide", "reason": "Жалоба подтверждена"},
        headers=admin_auth["headers"],
    )
    assert hide_res.status_code == 200
    assert hide_res.json()["status"] == "hidden"

    reports = await fetch(
        "SELECT status::text AS status FROM catalog_item_reports WHERE item_id = $1",
        item_id,
    )
    assert reports
    assert all(report["status"] != "open" for report in reports)
    audit = await fetchrow(
        """
        SELECT action FROM audit_logs
        WHERE resource_type = 'catalog_item' AND resource_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        str(item_id),
    )
    assert audit is not None
    assert audit["action"] == "admin.catalog.hide"
    notification = await fetchrow(
        """
        SELECT id FROM notifications
        WHERE href = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        f"/supplier/catalog/{item_id}",
    )
    assert notification is not None

    reject_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "reject", "reason": "Уточните описание"},
        headers=admin_auth["headers"],
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "changes_requested"
    assert reject_res.json()["action"] == "reject"
    reject_notification = await fetchrow(
        """
        SELECT title, body FROM notifications
        WHERE href = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        f"/supplier/catalog/{item_id}",
    )
    assert reject_notification is not None
    assert reject_notification["title"] == "Позиция отклонена"
    assert "отклонён(а) модератором" in reject_notification["body"]
    assert "Причина: Уточните описание" in reject_notification["body"]
    reject_audit = await fetchrow(
        """
        SELECT action FROM audit_logs
        WHERE resource_type = 'catalog_item' AND resource_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        str(item_id),
    )
    assert reject_audit is not None
    assert reject_audit["action"] == "admin.catalog.reject"

    delete_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "delete", "reason": "Повторные нарушения"},
        headers=admin_auth["headers"],
    )
    assert delete_res.status_code == 200
    assert delete_res.json()["status"] == "deleted"

    item_status = await fetchval(
        "SELECT status::text FROM catalog_items WHERE id = $1",
        item_id,
    )
    assert item_status == "deleted"

    public_res = await client.get(f"/api/v1/public/catalog/items/{item_id}")
    assert public_res.status_code == 404


@pytest.mark.asyncio
async def test_moderator_cannot_delete_catalog_item(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth)

    await upsert_moderator()

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "moderator@example.com", "password": "Moderator123!"},
    )
    assert login_res.status_code == 200
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    approve_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "approve"},
        headers=headers,
    )
    assert approve_res.status_code == 200

    delete_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "delete", "reason": "Недостаточно прав"},
        headers=headers,
    )
    assert delete_res.status_code == 403

    cleanup = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "delete", "reason": "Cleanup"},
        headers=admin_auth["headers"],
    )
    assert cleanup.status_code == 200


@pytest.mark.asyncio
async def test_negative_catalog_actions_require_reason(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_pending_item(client, supplier_auth)
    res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "hide"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 422
