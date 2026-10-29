import json
from uuid import uuid4

import pytest

from app.utils.storage import storage_service
from tests.sql import fetch, fetchrow, fetchval
from tests.staff import (
    create_role_with_permissions,
    create_staff,
    login_headers,
    unique_suffix,
)

API = "/api/v1"


async def _category_ids(client) -> list[int]:
    res = await client.get(f"{API}/public/categories")
    assert res.status_code == 200, res.text
    return [row["id"] for row in res.json()]


async def _create_item(
    client,
    supplier_auth,
    *,
    title: str,
    status: str = "pending_review",
    item_type: str = "product",
    price: float = 1000,
    category_id: int | None = None,
) -> int:
    if category_id is None:
        category_id = (await _category_ids(client))[0]
    res = await client.post(
        f"{API}/supplier/catalog/items",
        headers=supplier_auth["headers"],
        json={
            "type": item_type,
            "category_id": category_id,
            "title": title,
            "description": "Описание для админ-каталога",
            "status": status,
            "attributes": [{"name": "Материал", "value": "Сталь", "value_type": "text"}],
            "media": [
                {"file_name": "a.jpg", "file_url": "https://example.com/a.jpg", "media_type": "image"},
                {"file_name": "b.jpg", "file_url": "https://example.com/b.jpg", "media_type": "image"},
            ],
            "pricing": {
                "pricing_type": "fixed",
                "currency": "TJS",
                "fixed_price": price,
                "hourly_rate": None,
                "monthly_rate": None,
                "tiers": [],
            },
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def _last_audit(item_id: int):
    row = await fetchrow(
        """
        SELECT action, details FROM audit_logs
        WHERE resource_type = 'catalog_item' AND resource_id = $1
        ORDER BY id DESC LIMIT 1
        """,
        str(item_id),
    )
    assert row is not None
    details = row["details"]
    return row["action"], json.loads(details) if isinstance(details, str) else details


@pytest.mark.asyncio
async def test_catalog_items_list_filters(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    tag = uuid4().hex[:8]
    draft_id = await _create_item(client, supplier_auth, title=f"Черновик {tag}", status="draft", price=50)
    pending_id = await _create_item(
        client, supplier_auth, title=f"Услуга {tag}", item_type="service", price=5000
    )
    deleted_id = await _create_item(client, supplier_auth, title=f"Удалённый {tag}", price=700)
    delete_res = await client.post(
        f"{API}/admin/catalog/{deleted_id}/action",
        json={"action": "delete", "reason": "Тест"},
        headers=admin_auth["headers"],
    )
    assert delete_res.status_code == 200, delete_res.text

    res = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    data = res.json()
    ids = {item["id"] for item in data["items"]}
    assert {draft_id, pending_id} <= ids
    assert deleted_id not in ids
    assert data["status_counts"]["draft"] == 1
    assert data["status_counts"]["pending_review"] == 1
    assert data["status_counts"]["deleted"] == 1
    assert data["status_counts"]["all"] == 2
    first = next(item for item in data["items"] if item["id"] == pending_id)
    assert first["price"] == 5000
    assert first["currency"] == "TJS"
    assert first["updated_at"]
    assert first["owner"]["actor_id"] == supplier_auth["actor_id"]

    deleted_res = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "status": "deleted"},
        headers=admin_auth["headers"],
    )
    assert [item["id"] for item in deleted_res.json()["items"]] == [deleted_id]

    with_deleted = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "include_deleted": "true"},
        headers=admin_auth["headers"],
    )
    assert with_deleted.json()["total"] == 3

    services = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "type": "service"},
        headers=admin_auth["headers"],
    )
    assert [item["id"] for item in services.json()["items"]] == [pending_id]

    multi_status = await client.get(
        f"{API}/admin/catalog/items",
        params=[("query", tag), ("status", "draft"), ("status", "pending_review")],
        headers=admin_auth["headers"],
    )
    assert {item["id"] for item in multi_status.json()["items"]} == {draft_id, pending_id}

    priced = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "price_min": 100, "price_max": 6000, "sort": "price", "order": "asc"},
        headers=admin_auth["headers"],
    )
    assert [item["id"] for item in priced.json()["items"]] == [pending_id]

    by_title = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "sort": "title", "order": "asc"},
        headers=admin_auth["headers"],
    )
    titles = [item["title"] for item in by_title.json()["items"]]
    assert titles == sorted(titles, key=str.lower)

    company_id = await fetchval(
        "SELECT company_id FROM actors WHERE id = $1", supplier_auth["actor_id"]
    )
    if company_id:
        by_company = await client.get(
            f"{API}/admin/catalog/items",
            params={"query": tag, "company_id": company_id},
            headers=admin_auth["headers"],
        )
        assert {item["id"] for item in by_company.json()["items"]} == {draft_id, pending_id}
        other_company = await client.get(
            f"{API}/admin/catalog/items",
            params={"query": tag, "company_id": 999999},
            headers=admin_auth["headers"],
        )
        assert other_company.json()["total"] == 0

    by_actor = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "actor_id": supplier_auth["actor_id"], "page_size": 1},
        headers=admin_auth["headers"],
    )
    actor_data = by_actor.json()
    assert actor_data["total"] == 2
    assert actor_data["pages"] == 2
    assert len(actor_data["items"]) == 1

    reported = await client.get(
        f"{API}/admin/catalog/items",
        params={"query": tag, "has_reports": "true"},
        headers=admin_auth["headers"],
    )
    assert reported.json()["total"] == 0

    draft_detail = await client.get(
        f"{API}/admin/catalog/{draft_id}", headers=admin_auth["headers"]
    )
    assert draft_detail.status_code == 200, draft_detail.text
    assert draft_detail.json()["status"] == "draft"

    moderation_queue = await client.get(
        f"{API}/admin/catalog",
        params={"view": "draft", "query": tag},
        headers=admin_auth["headers"],
    )
    assert moderation_queue.status_code == 200
    assert [item["id"] for item in moderation_queue.json()["items"]] == [pending_id]


@pytest.mark.asyncio
async def test_admin_edits_catalog_item(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    categories = await _category_ids(client)
    item_id = await _create_item(client, supplier_auth, title=f"Правка {unique_suffix()}")
    before_updated = await fetchval("SELECT updated_at FROM catalog_items WHERE id = $1", item_id)

    res = await client.patch(
        f"{API}/admin/catalog/{item_id}",
        json={
            "title": "Исправленное название",
            "description": "Новое описание",
            "category_id": categories[-1],
            "pricing": {
                "pricing_type": "tiered",
                "currency": "usd",
                "tiers": [{"min_qty": 10, "price": 80}, {"min_qty": 1, "price": 100}],
            },
            "attributes": [
                {"name": "Материал", "value": "Алюминий"},
                {"name": "Вес", "value": "2 кг"},
            ],
            "reason": "Исправлены опечатки",
        },
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    detail = res.json()
    assert detail["title"] == "Исправленное название"
    assert detail["description"] == "Новое описание"
    assert detail["category"]["id"] == categories[-1]
    assert detail["pricing"]["pricing_type"] == "tiered"
    assert detail["pricing"]["currency"] == "USD"
    assert [tier["min_qty"] for tier in detail["pricing"]["tiers"]] == [1, 10]
    assert [(a["name"], a["value"]) for a in detail["attributes"]] == [
        ("Материал", "Алюминий"),
        ("Вес", "2 кг"),
    ]
    assert detail["status"] == "pending_review"
    after_updated = await fetchval("SELECT updated_at FROM catalog_items WHERE id = $1", item_id)
    assert after_updated >= before_updated

    action, details = await _last_audit(item_id)
    assert action == "admin.catalog.update"
    assert details["reason"] == "Исправлены опечатки"
    assert details["changes"]["title"]["after"] == "Исправленное название"
    assert "before" in details["changes"]["pricing"]
    assert "description" in details["fields"]
    if len(set(categories)) > 1:
        assert "category_id" in details["changes"]

    notification = await fetchrow(
        "SELECT title, body FROM notifications WHERE href = $1 ORDER BY id DESC LIMIT 1",
        f"/supplier/catalog/{item_id}",
    )
    assert notification["title"] == "Позиция изменена администратором"
    assert "Исправлены опечатки" in notification["body"]

    audit_count = await fetchval(
        "SELECT COUNT(*) FROM audit_logs WHERE resource_type = 'catalog_item' AND resource_id = $1",
        str(item_id),
    )
    noop = await client.patch(
        f"{API}/admin/catalog/{item_id}",
        json={"title": "Исправленное название"},
        headers=admin_auth["headers"],
    )
    assert noop.status_code == 200
    assert (
        await fetchval(
            "SELECT COUNT(*) FROM audit_logs WHERE resource_type = 'catalog_item' AND resource_id = $1",
            str(item_id),
        )
        == audit_count
    )

    bad_pricing = await client.patch(
        f"{API}/admin/catalog/{item_id}",
        json={"pricing": {"pricing_type": "fixed", "currency": "TJS"}},
        headers=admin_auth["headers"],
    )
    assert bad_pricing.status_code == 422

    bad_category = await client.patch(
        f"{API}/admin/catalog/{item_id}",
        json={"category_id": 99999999},
        headers=admin_auth["headers"],
    )
    assert bad_category.status_code == 422


@pytest.mark.asyncio
async def test_admin_manages_catalog_media(client, admin_auth, supplier_auth, monkeypatch):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_item(client, supplier_auth, title=f"Медиа {unique_suffix()}")

    uploaded_prefixes: list[str] = []

    async def _fake_upload(file, prefix="uploads"):
        uploaded_prefixes.append(prefix)
        key = f"{prefix}/{uuid4().hex}.png"
        return key, f"https://files.example.com/{key}"

    monkeypatch.setattr(storage_service, "upload", _fake_upload)

    upload = await client.post(
        f"{API}/admin/catalog/{item_id}/media",
        files={"file": ("photo.png", b"\x89PNG fake", "image/png")},
        headers=admin_auth["headers"],
    )
    assert upload.status_code == 200, upload.text
    media = upload.json()["media"]
    assert len(media) == 3
    assert media[-1]["file_name"] == "photo.png"
    assert media[-1]["media_type"] == "image"
    assert uploaded_prefixes == [f"catalog/{supplier_auth['actor_id']}"]
    action, details = await _last_audit(item_id)
    assert action == "admin.catalog.media.add"
    assert details["media_id"] == media[-1]["id"]

    reversed_ids = [row["id"] for row in reversed(media)]
    reorder = await client.put(
        f"{API}/admin/catalog/{item_id}/media/order",
        json={"media_ids": reversed_ids},
        headers=admin_auth["headers"],
    )
    assert reorder.status_code == 200, reorder.text
    assert [row["id"] for row in reorder.json()["media"]] == reversed_ids
    assert [row["sort_order"] for row in reorder.json()["media"]] == [0, 1, 2]
    action, details = await _last_audit(item_id)
    assert action == "admin.catalog.media.reorder"
    assert details["after"] == reversed_ids

    bad_reorder = await client.put(
        f"{API}/admin/catalog/{item_id}/media/order",
        json={"media_ids": reversed_ids[:1]},
        headers=admin_auth["headers"],
    )
    assert bad_reorder.status_code == 422

    removed_id = reversed_ids[0]
    delete = await client.delete(
        f"{API}/admin/catalog/{item_id}/media/{removed_id}",
        headers=admin_auth["headers"],
    )
    assert delete.status_code == 200, delete.text
    remaining = delete.json()["media"]
    assert [row["id"] for row in remaining] == reversed_ids[1:]
    assert [row["sort_order"] for row in remaining] == [0, 1]
    action, details = await _last_audit(item_id)
    assert action == "admin.catalog.media.delete"
    assert details["media_id"] == removed_id

    missing = await client.delete(
        f"{API}/admin/catalog/{item_id}/media/{removed_id}",
        headers=admin_auth["headers"],
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_admin_restores_hidden_and_deleted_items(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_item(client, supplier_auth, title=f"Восстановление {unique_suffix()}")

    bad_restore = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "restore"},
        headers=admin_auth["headers"],
    )
    assert bad_restore.status_code == 422

    for action, reason in (("approve", None), ("hide", "Проверка")):
        res = await client.post(
            f"{API}/admin/catalog/{item_id}/action",
            json={"action": action, "reason": reason},
            headers=admin_auth["headers"],
        )
        assert res.status_code == 200, res.text

    unhide = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "restore"},
        headers=admin_auth["headers"],
    )
    assert unhide.status_code == 200, unhide.text
    assert unhide.json()["status"] == "active"
    action, details = await _last_audit(item_id)
    assert action == "admin.catalog.restore"
    assert details["previous_status"] == "hidden"

    deleted = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "delete", "reason": "Ошибка"},
        headers=admin_auth["headers"],
    )
    assert deleted.status_code == 200

    edit_deleted = await client.patch(
        f"{API}/admin/catalog/{item_id}",
        json={"title": "Нельзя"},
        headers=admin_auth["headers"],
    )
    assert edit_deleted.status_code == 422
    hide_deleted = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "hide", "reason": "x"},
        headers=admin_auth["headers"],
    )
    assert hide_deleted.status_code == 422

    detail = await client.get(f"{API}/admin/catalog/{item_id}", headers=admin_auth["headers"])
    assert detail.status_code == 200
    assert detail.json()["status"] == "deleted"

    restore = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "restore"},
        headers=admin_auth["headers"],
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["status"] == "active"
    action, details = await _last_audit(item_id)
    assert action == "admin.catalog.restore"
    assert details["previous_status"] == "deleted"
    public = await client.get(f"{API}/public/catalog/items/{item_id}")
    assert public.status_code == 200


@pytest.mark.asyncio
async def test_admin_bulk_catalog_actions(client, admin_auth, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    tag = unique_suffix()
    ids = [
        await _create_item(client, supplier_auth, title=f"Пакет {tag} {idx}") for idx in range(3)
    ]

    no_reason = await client.post(
        f"{API}/admin/catalog/bulk-action",
        json={"ids": ids, "action": "hide"},
        headers=admin_auth["headers"],
    )
    assert no_reason.status_code == 422

    approve = await client.post(
        f"{API}/admin/catalog/bulk-action",
        json={"ids": ids + [ids[0], 99999999], "action": "approve"},
        headers=admin_auth["headers"],
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["succeeded"] == ids
    assert [row["id"] for row in body["failed"]] == [99999999]
    statuses = await fetch(
        "SELECT status::text AS status FROM catalog_items WHERE id = ANY($1::int[])", ids
    )
    assert {row["status"] for row in statuses} == {"active"}

    hide = await client.post(
        f"{API}/admin/catalog/bulk-action",
        json={"ids": ids[:2], "action": "hide", "reason": "Массовая проверка"},
        headers=admin_auth["headers"],
    )
    assert hide.status_code == 200, hide.text
    assert hide.json()["succeeded"] == ids[:2]
    for item_id in ids[:2]:
        action, details = await _last_audit(item_id)
        assert action == "admin.catalog.hide"
        assert details["bulk"] is True
        assert details["reason"] == "Массовая проверка"

    delete = await client.post(
        f"{API}/admin/catalog/bulk-action",
        json={"ids": ids, "action": "delete", "reason": "Очистка"},
        headers=admin_auth["headers"],
    )
    assert delete.status_code == 200, delete.text
    assert delete.json()["succeeded"] == ids
    hidden_notifications = await fetchval(
        "SELECT COUNT(*) FROM notifications WHERE href = ANY($1::text[]) AND title = 'Позиция удалена'",
        [f"/supplier/catalog/{item_id}" for item_id in ids],
    )
    assert hidden_notifications == 3


@pytest.mark.asyncio
async def test_staff_without_catalog_update_is_denied(client, admin_auth, supplier_auth, monkeypatch):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    item_id = await _create_item(client, supplier_auth, title=f"Права {unique_suffix()}")
    media_id = await fetchval(
        "SELECT id FROM item_media WHERE item_id = $1 ORDER BY sort_order LIMIT 1", item_id
    )

    role_id = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"catalog-view-{unique_suffix()}",
        ["admin_catalog.view"],
    )
    staff = await create_staff(client, admin_auth["headers"], role_id)
    headers = await login_headers(client, staff["email"])

    listing = await client.get(f"{API}/admin/catalog/items", headers=headers)
    assert listing.status_code == 200, listing.text
    detail = await client.get(f"{API}/admin/catalog/{item_id}", headers=headers)
    assert detail.status_code == 200

    async def _fail_upload(*_args, **_kwargs):
        raise AssertionError("upload must not be reached")

    monkeypatch.setattr(storage_service, "upload", _fail_upload)

    responses = [
        await client.patch(f"{API}/admin/catalog/{item_id}", json={"title": "Взлом"}, headers=headers),
        await client.post(
            f"{API}/admin/catalog/{item_id}/media",
            files={"file": ("x.png", b"x", "image/png")},
            headers=headers,
        ),
        await client.delete(f"{API}/admin/catalog/{item_id}/media/{media_id}", headers=headers),
        await client.put(
            f"{API}/admin/catalog/{item_id}/media/order",
            json={"media_ids": [media_id]},
            headers=headers,
        ),
        await client.post(
            f"{API}/admin/catalog/bulk-action",
            json={"ids": [item_id], "action": "approve"},
            headers=headers,
        ),
        await client.post(
            f"{API}/admin/catalog/{item_id}/action",
            json={"action": "restore"},
            headers=headers,
        ),
    ]
    assert [res.status_code for res in responses] == [403] * len(responses)

    update_role = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"catalog-update-{unique_suffix()}",
        ["admin_catalog.view", "admin_catalog.update"],
    )
    editor = await create_staff(client, admin_auth["headers"], update_role)
    editor_headers = await login_headers(client, editor["email"])
    bulk_delete = await client.post(
        f"{API}/admin/catalog/bulk-action",
        json={"ids": [item_id], "action": "delete", "reason": "Нет прав"},
        headers=editor_headers,
    )
    assert bulk_delete.status_code == 403
    edit = await client.patch(
        f"{API}/admin/catalog/{item_id}",
        json={"title": "Редактор поправил"},
        headers=editor_headers,
    )
    assert edit.status_code == 200, edit.text
    assert await fetchval("SELECT title FROM catalog_items WHERE id = $1", item_id) == "Редактор поправил"
