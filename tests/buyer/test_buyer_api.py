import pytest

from tests.sql import execute

API = "/api/v1"


async def _create_draft_rfq(client, buyer_auth, title: str) -> str:
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


async def _set_rfq_status(rfq_id: str, status: str) -> None:
    await execute(
        "UPDATE rfqs SET status = $1::rfq_status WHERE id = $2",
        status,
        rfq_id,
    )


@pytest.mark.asyncio
async def test_buyer_reviews_list(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    res = await client.get(f"{API}/buyer/reviews/", headers=buyer_auth["headers"])
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert body["page"] == 1
    assert body["page_size"] == 20


@pytest.mark.asyncio
async def test_buyer_review_missing_contract(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    res = await client.post(
        f"{API}/buyer/reviews/",
        headers=buyer_auth["headers"],
        json={
            "contract_id": 999999,
            "target_actor_id": buyer_auth["actor_id"],
            "rating": 5,
            "comment": "test",
        },
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_public_rfqs(client):
    res = await client.get(f"{API}/public/rfqs")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert body["page"] == 1
    assert body["page_size"] == 20


@pytest.mark.asyncio
async def test_public_catalog(client):
    res = await client.get(f"{API}/public/catalog")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert body["page"] == 1
    assert body["page_size"] == 20

    filtered = await client.get(
        f"{API}/public/catalog",
        params={"category_id": 1, "catalog_type": "product"},
    )
    assert filtered.status_code == 200
    assert isinstance(filtered.json()["items"], list)

    verified = await client.get(
        f"{API}/public/catalog",
        params={"verified": "true", "invoice": "true", "sort": "cheap"},
    )
    assert verified.status_code == 200
    verified_body = verified.json()
    assert isinstance(verified_body["items"], list)
    for item in verified_body["items"]:
        assert item.get("invoice") is True

    invalid = await client.get(
        f"{API}/public/catalog",
        params={"catalog_type": "invalid"},
    )
    assert invalid.status_code == 422

    invalid_sort = await client.get(
        f"{API}/public/catalog",
        params={"sort": "unknown"},
    )
    assert invalid_sort.status_code == 422


@pytest.mark.asyncio
async def test_public_suppliers_verified(client):
    res = await client.get(f"{API}/public/suppliers", params={"verified": "true"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    for supplier in body["items"]:
        assert supplier["verification_status"] == "verified"


@pytest.mark.asyncio
async def test_buyer_rfqs_list(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    res = await client.get(f"{API}/buyer/rfqs/", headers=buyer_auth["headers"])
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert body["page"] == 1
    assert body["page_size"] == 20


@pytest.mark.asyncio
async def test_buyer_rfqs_list_all_and_filter_by_status(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")

    draft_id = await _create_draft_rfq(client, buyer_auth, "Buyer list draft RFQ")
    completed_id = await _create_draft_rfq(client, buyer_auth, "Buyer list completed RFQ")
    await _set_rfq_status(completed_id, "completed")

    all_res = await client.get(
        f"{API}/buyer/rfqs/", params={"page_size": 100}, headers=buyer_auth["headers"]
    )
    assert all_res.status_code == 200, all_res.text
    all_ids = {item["id"] for item in all_res.json()["items"]}
    assert draft_id in all_ids
    assert completed_id in all_ids

    draft_res = await client.get(
        f"{API}/buyer/rfqs/",
        params={"tab": "draft", "page_size": 100},
        headers=buyer_auth["headers"],
    )
    assert draft_res.status_code == 200, draft_res.text
    draft_items = draft_res.json()["items"]
    assert all(item["status"] == "draft" for item in draft_items)
    assert draft_id in {item["id"] for item in draft_items}
    assert completed_id not in {item["id"] for item in draft_items}

    completed_res = await client.get(
        f"{API}/buyer/rfqs/",
        params={"tab": "completed", "page_size": 100},
        headers=buyer_auth["headers"],
    )
    assert completed_res.status_code == 200, completed_res.text
    completed_items = completed_res.json()["items"]
    assert all(
        item["status"] in {"completed", "cancelled", "expired"} for item in completed_items
    )
    assert completed_id in {item["id"] for item in completed_items}
    assert draft_id not in {item["id"] for item in completed_items}


@pytest.mark.asyncio
async def test_public_suppliers_category_filter(client):
    res = await client.get(f"{API}/public/suppliers?category=it")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert body["page"] == 1
    assert body["page_size"] == 20
