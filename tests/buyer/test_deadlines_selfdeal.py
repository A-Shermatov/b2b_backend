import pytest

from tests.conftest import _login
from tests.sql import execute, fetchval

API = "/api/v1"
SELF_DEAL_MESSAGE = "Нельзя заключить сделку с самим собой"
DEADLINE_MESSAGE = "Срок приёма предложений истёк"


def _rfq_payload(title: str, deadline: str = "2099-12-31") -> dict:
    return {
        "type": "service",
        "title": title,
        "category_id": "it",
        "budget_type": "open",
        "currency": "TJS",
        "deadline": deadline,
        "visibility": "public",
        "status": "draft",
        "project_duration": "2 months",
        "start_date": "2026-07-01",
    }


async def _publish_rfq(client, buyer_headers: dict, title: str) -> str:
    create_res = await client.post(
        f"{API}/buyer/rfqs/", headers=buyer_headers, json=_rfq_payload(title)
    )
    assert create_res.status_code == 200, create_res.text
    rfq_id = create_res.json()["id"]
    publish_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_headers
    )
    assert publish_res.status_code == 200, publish_res.text
    return rfq_id


async def _make_overdue(rfq_id: str) -> None:
    await execute("UPDATE rfqs SET deadline = '2020-01-01' WHERE id = $1", rfq_id)


async def _submit(client, supplier_headers: dict, rfq_id: str):
    return await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_headers,
        json={"price": 1000, "currency": "TJS", "delivery_time": "10 days"},
    )


@pytest.mark.asyncio
async def test_proposal_rejected_after_deadline(client, buyer_auth, supplier_auth):
    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline submit RFQ")
    await _make_overdue(rfq_id)

    res = await _submit(client, supplier_auth["headers"], rfq_id)
    assert res.status_code == 409, res.text
    assert DEADLINE_MESSAGE in res.text


@pytest.mark.asyncio
async def test_proposal_update_rejected_after_deadline(
    client, buyer_auth, supplier_auth
):
    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline update RFQ")
    submit_res = await _submit(client, supplier_auth["headers"], rfq_id)
    assert submit_res.status_code == 200, submit_res.text
    proposal_id = submit_res.json()["id"]
    await _make_overdue(rfq_id)

    res = await client.patch(
        f"{API}/supplier/proposals/{proposal_id}",
        headers=supplier_auth["headers"],
        json={"price": 900},
    )
    assert res.status_code == 409, res.text
    assert DEADLINE_MESSAGE in res.text


@pytest.mark.asyncio
async def test_overdue_rfq_expires_and_leaves_boards(client, buyer_auth, supplier_auth):
    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline board RFQ")
    await _make_overdue(rfq_id)

    board = await client.get(
        f"{API}/supplier/rfqs/board", headers=supplier_auth["headers"]
    )
    assert board.status_code == 200, board.text
    assert all(item["id"] != rfq_id for item in board.json()["items"])
    assert (
        await fetchval("SELECT status::text FROM rfqs WHERE id = $1", rfq_id)
        == "expired"
    )

    public = await client.get(f"{API}/public/rfqs")
    assert public.status_code == 200, public.text
    assert all(item["id"] != rfq_id for item in public.json()["items"])

    mine = await client.get(
        f"{API}/buyer/rfqs/", headers=buyer_auth["headers"], params={"tab": "expired"}
    )
    assert mine.status_code == 200, mine.text
    assert any(item["id"] == rfq_id for item in mine.json()["items"])


@pytest.mark.asyncio
async def test_buyer_can_accept_proposal_after_rfq_expired(
    client, buyer_auth, supplier_auth
):
    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline accept RFQ")
    submit_res = await _submit(client, supplier_auth["headers"], rfq_id)
    assert submit_res.status_code == 200, submit_res.text
    proposal_id = submit_res.json()["id"]
    await _make_overdue(rfq_id)

    await client.get(f"{API}/supplier/rfqs/board", headers=supplier_auth["headers"])
    assert (
        await fetchval("SELECT status::text FROM rfqs WHERE id = $1", rfq_id)
        == "expired"
    )

    accept_res = await client.post(
        f"{API}/buyer/proposals/{proposal_id}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": "full_prepayment"},
    )
    assert accept_res.status_code == 200, accept_res.text


@pytest.mark.asyncio
async def test_rfq_with_contract_is_not_expired(client, buyer_auth, supplier_auth):
    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline contract RFQ")
    submit_res = await _submit(client, supplier_auth["headers"], rfq_id)
    proposal_id = submit_res.json()["id"]
    accept_res = await client.post(
        f"{API}/buyer/proposals/{proposal_id}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": "full_prepayment"},
    )
    assert accept_res.status_code == 200, accept_res.text
    await _make_overdue(rfq_id)

    await client.get(f"{API}/supplier/rfqs/board", headers=supplier_auth["headers"])
    assert (
        await fetchval("SELECT status::text FROM rfqs WHERE id = $1", rfq_id)
        == "contract_created"
    )


@pytest.mark.asyncio
async def test_supplier_cannot_propose_on_own_rfq(client):
    both_buyer = await _login(client, "both@example.com", "Both123!", side="buyer")
    both_supplier = await _login(client, "both@example.com", "Both123!", side="supplier")
    if not both_buyer["actor_id"] or not both_supplier["actor_id"]:
        pytest.skip("both@example.com has no buyer/supplier actors")

    rfq_id = await _publish_rfq(client, both_buyer["headers"], "Self deal own RFQ")

    res = await _submit(client, both_supplier["headers"], rfq_id)
    assert res.status_code == 409, res.text
    assert SELF_DEAL_MESSAGE in res.text


@pytest.mark.asyncio
async def test_supplier_accept_rejects_self_dealing(
    client, buyer_auth, supplier_auth, admin_auth
):
    from tests.buyer.test_catalog_offers import _create_active_item

    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)
    offer_res = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy"},
    )
    assert offer_res.status_code == 200, offer_res.text
    offer = offer_res.json()
    await execute(
        "UPDATE rfqs SET actor_id = $1 WHERE id = $2",
        supplier_auth["actor_id"],
        offer["rfq_id"],
    )

    res = await client.post(
        f"{API}/supplier/proposals/{offer['proposal_id']}/accept",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 409, res.text
    assert SELF_DEAL_MESSAGE in res.text
