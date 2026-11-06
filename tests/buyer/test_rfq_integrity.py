import pytest

API = "/api/v1"


async def _create_rfq(client, buyer_auth, title: str, status: str = "draft") -> str:
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
            "status": status,
            "project_duration": "2 months",
            "start_date": "2026-07-01",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


@pytest.mark.asyncio
async def test_rfq_patch_ignores_status_and_created_by(client, buyer_auth):
    rfq_id = await _create_rfq(client, buyer_auth, "Integrity patch RFQ")
    res = await client.patch(
        f"{API}/buyer/rfqs/{rfq_id}",
        headers=buyer_auth["headers"],
        json={"title": "Integrity patched RFQ", "status": "completed", "created_by": "999999"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "Integrity patched RFQ"
    assert body["status"] == "draft"
    assert body["created_by"] != "999999"


@pytest.mark.asyncio
async def test_rfq_close_only_from_open_statuses(client, buyer_auth):
    rfq_id = await _create_rfq(client, buyer_auth, "Integrity close RFQ", "published")
    res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/close", headers=buyer_auth["headers"]
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "cancelled"

    again = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/close", headers=buyer_auth["headers"]
    )
    assert again.status_code == 409, again.text


@pytest.mark.asyncio
async def test_rfq_hidden_by_admin_moves_to_the_buyer_archive(client, buyer_auth, admin_auth):
    rfq_id = await _create_rfq(client, buyer_auth, "Archive after moderation", status="published")
    res = await client.post(
        f"{API}/admin/rfqs/{rfq_id}/action",
        json={"action": "hide", "reason": "Запрещённый товар"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text

    everything = await client.get(f"{API}/buyer/rfqs/", params={"tab": "all", "page_size": 100}, headers=buyer_auth["headers"])
    assert rfq_id not in [r["id"] for r in everything.json()["items"]]

    archive = await client.get(
        f"{API}/buyer/rfqs/", params={"tab": "closed", "q": "Archive after moderation", "page_size": 100},
        headers=buyer_auth["headers"],
    )
    assert archive.status_code == 200, archive.text
    found = next(r for r in archive.json()["items"] if r["id"] == rfq_id)
    assert found["status"] == "archived"
    assert found["moderation_reason"] == "Запрещённый товар"
    assert found["moderated_at"]
