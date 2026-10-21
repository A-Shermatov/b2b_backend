import pytest

from tests.sql import execute, fetchrow


async def _create_active_item(client, supplier_auth, admin_auth, *, with_price=True) -> int:
    # Free plan slots left by prior tests in the shared DB.
    await execute(
        """
        UPDATE catalog_items
        SET status = 'archived'::item_status
        WHERE actor_id = $1
          AND status::text IN ('active', 'pending_review', 'changes_requested', 'hidden')
        """,
        supplier_auth["actor_id"],
    )
    categories = await client.get("/api/v1/public/categories")
    assert categories.status_code == 200
    category_id = categories.json()[0]["id"]
    pricing = (
        {
            "pricing_type": "fixed",
            "currency": "TJS",
            "fixed_price": 2500,
            "hourly_rate": None,
            "monthly_rate": None,
            "tiers": [],
        }
        if with_price
        else {
            "pricing_type": "fixed",
            "currency": "TJS",
            "fixed_price": None,
            "hourly_rate": None,
            "monthly_rate": None,
            "tiers": [],
        }
    )
    create_res = await client.post(
        "/api/v1/supplier/catalog/items",
        headers=supplier_auth["headers"],
        json={
            "type": "service",
            "category_id": category_id,
            "title": "Услуга для оффера",
            "description": "Описание услуги из каталога",
            "status": "pending_review",
            "attributes": [],
            "media": [],
            "pricing": pricing,
        },
    )
    assert create_res.status_code == 200, create_res.text
    item_id = create_res.json()["id"]
    approve_res = await client.post(
        f"/api/v1/admin/catalog/{item_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert approve_res.status_code == 200, approve_res.text
    return item_id


@pytest.mark.asyncio
async def test_buy_offer_supplier_accept_creates_contract(
    client, buyer_auth, supplier_auth, admin_auth
):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)

    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy"},
    )
    assert offer_res.status_code == 200, offer_res.text
    offer = offer_res.json()
    assert offer["price"] == 2500
    assert offer["currency"] == "TJS"
    proposal_id = offer["proposal_id"]

    rfq_row = await fetchrow(
        "SELECT catalog_item_id FROM rfqs WHERE id = $1",
        offer["rfq_id"],
    )
    assert rfq_row["catalog_item_id"] == item_id

    proposal_row = await fetchrow(
        "SELECT status::text AS status, price FROM proposals WHERE id = $1",
        proposal_id,
    )
    assert proposal_row["status"] == "pending_supplier"
    assert float(proposal_row["price"]) == 2500

    accept_res = await client.post(
        f"/api/v1/supplier/proposals/{proposal_id}/accept",
        headers=supplier_auth["headers"],
    )
    assert accept_res.status_code == 200, accept_res.text
    body = accept_res.json()
    assert body["contract_id"]
    assert body["proposal"]["status"] == "accepted"

    contract = await fetchrow(
        "SELECT agreed_amount, payment_type::text AS payment_type, status::text AS status "
        "FROM contracts WHERE id = $1",
        body["contract_id"],
    )
    assert float(contract["agreed_amount"]) == 2500
    assert contract["payment_type"] == "full_postpayment"
    assert contract["status"] == "pending_payment"


@pytest.mark.asyncio
async def test_catalog_offer_appears_on_supplier_board(
    client, buyer_auth, supplier_auth, admin_auth
):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)

    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "negotiate", "price": 1500, "message": "Торг с доски"},
    )
    assert offer_res.status_code == 200, offer_res.text
    rfq_id = offer_res.json()["rfq_id"]

    board_res = await client.get(
        "/api/v1/supplier/rfqs/board",
        headers=supplier_auth["headers"],
    )
    assert board_res.status_code == 200
    item = next((row for row in board_res.json()["items"] if row["id"] == rfq_id), None)
    assert item is not None
    assert item["is_catalog_offer"] is True
    assert item["my_proposal_status"] == "pending_supplier"


@pytest.mark.asyncio
async def test_negotiate_offer_flow(client, buyer_auth, supplier_auth, admin_auth):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)

    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "negotiate", "price": 1800, "message": "Могу за 1800"},
    )
    assert offer_res.status_code == 200, offer_res.text
    offer = offer_res.json()
    assert offer["price"] == 1800
    proposal_id = offer["proposal_id"]

    accept_res = await client.post(
        f"/api/v1/supplier/proposals/{proposal_id}/accept",
        headers=supplier_auth["headers"],
    )
    assert accept_res.status_code == 200, accept_res.text
    contract = await fetchrow(
        "SELECT agreed_amount FROM contracts WHERE id = $1",
        accept_res.json()["contract_id"],
    )
    assert float(contract["agreed_amount"]) == 1800


@pytest.mark.asyncio
async def test_buy_without_price_rejected(client, buyer_auth, supplier_auth, admin_auth):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(
        client, supplier_auth, admin_auth, with_price=False
    )

    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy"},
    )
    assert offer_res.status_code == 422


@pytest.mark.asyncio
async def test_negotiate_requires_message(client, buyer_auth, supplier_auth, admin_auth):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)

    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "negotiate", "price": 1200, "message": ""},
    )
    assert offer_res.status_code == 422


@pytest.mark.asyncio
async def test_buyer_cannot_accept_pending_supplier(
    client, buyer_auth, supplier_auth, admin_auth
):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)
    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy"},
    )
    assert offer_res.status_code == 200
    proposal_id = offer_res.json()["proposal_id"]

    accept_res = await client.post(
        f"/api/v1/buyer/proposals/{proposal_id}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": "full_postpayment"},
    )
    assert accept_res.status_code == 409


@pytest.mark.asyncio
async def test_supplier_reject_and_buyer_withdraw(
    client, buyer_auth, supplier_auth, admin_auth
):
    if not supplier_auth["actor_id"] or not buyer_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")
    item_id = await _create_active_item(client, supplier_auth, admin_auth)

    offer_res = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "negotiate", "price": 1000, "message": "Торг"},
    )
    assert offer_res.status_code == 200
    proposal_id = offer_res.json()["proposal_id"]

    reject_res = await client.post(
        f"/api/v1/supplier/proposals/{proposal_id}/reject",
        headers=supplier_auth["headers"],
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "rejected"

    item_id2 = await _create_active_item(client, supplier_auth, admin_auth)
    offer2 = await client.post(
        f"/api/v1/buyer/catalog/items/{item_id2}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy"},
    )
    assert offer2.status_code == 200
    withdraw_res = await client.post(
        f"/api/v1/buyer/proposals/{offer2.json()['proposal_id']}/withdraw",
        headers=buyer_auth["headers"],
    )
    assert withdraw_res.status_code == 200
    assert withdraw_res.json()["status"] == "withdrawn"
