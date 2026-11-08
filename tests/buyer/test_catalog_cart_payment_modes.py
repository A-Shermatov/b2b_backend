"""Buying from the catalog: quantity, price requests, the cart, paying at once and item reviews."""

import pytest

from tests.sql import execute, fetchrow, fetchval

API = "/api/v1"


async def _active_item(client, supplier_auth, admin_auth, pricing: dict, *, kind="product") -> int:
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
    categories = await client.get(f"{API}/public/categories")
    category_id = categories.json()[0]["id"]
    created = await client.post(
        f"{API}/supplier/catalog/items",
        headers=supplier_auth["headers"],
        json={
            "type": kind,
            "category_id": category_id,
            "title": "Бумага офисная А4",
            "description": "Пачка 500 листов",
            "status": "pending_review",
            "attributes": [],
            "media": [],
            "pricing": {
                "pricing_type": "fixed",
                "currency": "TJS",
                "fixed_price": None,
                "hourly_rate": None,
                "monthly_rate": None,
                "tiers": [],
                **pricing,
            },
        },
    )
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    approved = await client.post(
        f"{API}/admin/catalog/{item_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert approved.status_code == 200, approved.text
    return item_id


TIERED = {
    "pricing_type": "tiered",
    "tiers": [{"min_qty": 10, "price": 50}, {"min_qty": 100, "price": 40}],
}


@pytest.mark.asyncio
async def test_buy_prices_the_whole_order_by_tier(client, buyer_auth, supplier_auth, admin_auth):
    item_id = await _active_item(client, supplier_auth, admin_auth, TIERED)

    too_few = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy", "qty": 5},
    )
    assert too_few.status_code == 422, too_few.text

    offer = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "buy", "qty": 120, "payment_mode": "direct"},
    )
    assert offer.status_code == 200, offer.text
    body = offer.json()
    # 120 reaches the 100+ tier: 120 × 40.
    assert body["price"] == 4800
    assert body["qty"] == 120
    assert body["payment_mode"] == "direct"
    rfq = await fetchrow(
        "SELECT payment_mode, quantity, budget_from FROM rfqs WHERE id = $1", body["rfq_id"]
    )
    assert rfq["payment_mode"] == "direct"
    assert rfq["quantity"] == 120
    assert float(rfq["budget_from"]) == 4800


@pytest.mark.asyncio
async def test_price_request_invites_the_supplier_without_an_offer(
    client, buyer_auth, supplier_auth, admin_auth
):
    item_id = await _active_item(client, supplier_auth, admin_auth, {})

    res = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=buyer_auth["headers"],
        json={"kind": "quote", "qty": 30, "message": "Нужна доставка в Худжанд"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["proposal_id"] is None
    assert body["price"] is None
    rfq = await fetchrow(
        "SELECT budget_type::text AS budget_type, status::text AS status, description "
        "FROM rfqs WHERE id = $1",
        body["rfq_id"],
    )
    assert rfq["budget_type"] == "open"
    assert rfq["status"] == "receiving_proposals"
    assert "Запрос цены" in rfq["description"]
    assert "Худжанд" in rfq["description"]
    invited = await fetchval(
        "SELECT COUNT(*) FROM rfq_invited_suppliers WHERE rfq_id = $1 AND supplier_actor_id = $2",
        body["rfq_id"],
        supplier_auth["actor_id"],
    )
    assert invited == 1
    proposals = await fetchval("SELECT COUNT(*) FROM proposals WHERE rfq_id = $1", body["rfq_id"])
    assert proposals == 0


@pytest.mark.asyncio
async def test_cart_buys_priced_lines_and_asks_price_for_the_rest(
    client, buyer_auth, supplier_auth, admin_auth
):
    priced = await _active_item(client, supplier_auth, admin_auth, {"fixed_price": 300})
    # The same supplier may list several items: keep the first one active.
    await execute(
        "UPDATE catalog_items SET status = 'active'::item_status WHERE id = $1", priced
    )
    unpriced_res = await client.post(
        f"{API}/supplier/catalog/items",
        headers=supplier_auth["headers"],
        json={
            "type": "service",
            "category_id": (await client.get(f"{API}/public/categories")).json()[0]["id"],
            "title": "Монтаж стеллажей",
            "description": "Под ключ",
            "status": "pending_review",
            "attributes": [],
            "media": [],
            "pricing": {
                "pricing_type": "fixed",
                "currency": "TJS",
                "fixed_price": None,
                "hourly_rate": None,
                "monthly_rate": None,
                "tiers": [],
            },
        },
    )
    assert unpriced_res.status_code == 200, unpriced_res.text
    unpriced = unpriced_res.json()["id"]
    await client.post(
        f"{API}/admin/catalog/{unpriced}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )

    res = await client.post(
        f"{API}/buyer/approvals/checkout",
        headers=buyer_auth["headers"],
        json={
            "items": [
                {"item_id": priced, "qty": 3, "mode": "buy"},
                {"item_id": unpriced, "qty": 1, "mode": "quote"},
            ],
            "payment_mode": "direct",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Only the priced line counts towards the total.
    assert body["approval"]["amount"] == 900
    if not body["offers_created"]:
        pytest.skip("This buyer's role sends carts for approval first")
    assert len(body["rfq_ids"]) == 2
    rows = await fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE payment_mode = 'direct') AS direct,
            COUNT(*) FILTER (WHERE budget_type::text = 'open') AS open_budget
        FROM rfqs WHERE id = ANY($1::text[])
        """,
        body["rfq_ids"],
    )
    assert rows["direct"] == 2
    assert rows["open_budget"] == 1


@pytest.mark.asyncio
async def test_paying_at_once_sends_the_money_straight_to_the_supplier(
    client, buyer_auth, supplier_auth, admin_auth
):
    item_id = await _active_item(client, supplier_auth, admin_auth, {"fixed_price": 1200})
    offer = (
        await client.post(
            f"{API}/buyer/catalog/items/{item_id}/offers",
            headers=buyer_auth["headers"],
            json={"kind": "buy", "qty": 2, "payment_mode": "direct"},
        )
    ).json()
    accepted = await client.post(
        f"{API}/supplier/proposals/{offer['proposal_id']}/accept",
        headers=supplier_auth["headers"],
    )
    assert accepted.status_code == 200, accepted.text
    contract_id = accepted.json()["contract_id"]

    contract = (
        await client.get(f"{API}/buyer/contracts/{contract_id}", headers=buyer_auth["headers"])
    ).json()
    assert contract["payment_mode"] == "direct"
    assert contract["agreed_amount"] == 2400
    milestones = contract["payment_plan"]["milestones"]
    # One payment up front, nothing waiting for acceptance.
    assert [m["percentage"] for m in milestones] == [100]

    fund = await client.post(
        f"{API}/buyer/payments/milestones/{milestones[0]['id']}/fund",
        headers=buyer_auth["headers"],
    )
    assert fund.status_code == 200, fund.text
    assert fund.json()["status"] == "released"
    status = await fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", milestones[0]["id"]
    )
    assert status == "released"

    # Acceptance still closes the deal, with nothing left to release.
    submit = await client.post(
        f"{API}/supplier/contracts/{contract_id}/submissions",
        headers=supplier_auth["headers"],
        json={"type": "work", "note": "Отгружено", "file_names": [], "assets": []},
    )
    assert submit.status_code == 200, submit.text
    submission_id = submit.json()["submissions"][-1]["id"]
    approve = await client.post(
        f"{API}/buyer/contracts/{contract_id}/submissions/{submission_id}/approve",
        headers=buyer_auth["headers"],
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_escrow_stays_held_until_acceptance(client, buyer_auth, supplier_auth, admin_auth):
    item_id = await _active_item(client, supplier_auth, admin_auth, {"fixed_price": 700})
    offer = (
        await client.post(
            f"{API}/buyer/catalog/items/{item_id}/offers",
            headers=buyer_auth["headers"],
            json={"kind": "buy"},
        )
    ).json()
    accepted = await client.post(
        f"{API}/supplier/proposals/{offer['proposal_id']}/accept",
        headers=supplier_auth["headers"],
    )
    contract_id = accepted.json()["contract_id"]
    contract = (
        await client.get(f"{API}/buyer/contracts/{contract_id}", headers=buyer_auth["headers"])
    ).json()
    assert contract["payment_mode"] == "escrow"
    milestone = contract["payment_plan"]["milestones"][0]
    fund = await client.post(
        f"{API}/buyer/payments/milestones/{milestone['id']}/fund",
        headers=buyer_auth["headers"],
    )
    assert fund.status_code == 200, fund.text
    assert fund.json()["status"] == "funded"


@pytest.mark.asyncio
async def test_item_reviews_come_from_finished_catalog_deals(
    client, buyer_auth, supplier_auth, admin_auth
):
    item_id = await _active_item(client, supplier_auth, admin_auth, {"fixed_price": 900})

    empty = (await client.get(f"{API}/public/catalog/items/{item_id}/reviews")).json()
    assert empty["total"] == 0
    assert empty["summary"]["count"] == 0

    before = (
        await client.get(
            f"{API}/buyer/reviews/catalog-items/{item_id}/eligibility",
            headers=buyer_auth["headers"],
        )
    ).json()
    assert before["contract_id"] is None

    offer = (
        await client.post(
            f"{API}/buyer/catalog/items/{item_id}/offers",
            headers=buyer_auth["headers"],
            json={"kind": "buy", "payment_mode": "direct"},
        )
    ).json()
    contract_id = (
        await client.post(
            f"{API}/supplier/proposals/{offer['proposal_id']}/accept",
            headers=supplier_auth["headers"],
        )
    ).json()["contract_id"]
    contract = (
        await client.get(f"{API}/buyer/contracts/{contract_id}", headers=buyer_auth["headers"])
    ).json()
    for milestone in contract["payment_plan"]["milestones"]:
        await client.post(
            f"{API}/buyer/payments/milestones/{milestone['id']}/fund",
            headers=buyer_auth["headers"],
        )
    submission_id = (
        await client.post(
            f"{API}/supplier/contracts/{contract_id}/submissions",
            headers=supplier_auth["headers"],
            json={"type": "work", "note": "Готово", "file_names": [], "assets": []},
        )
    ).json()["submissions"][-1]["id"]
    done = await client.post(
        f"{API}/buyer/contracts/{contract_id}/submissions/{submission_id}/approve",
        headers=buyer_auth["headers"],
    )
    assert done.status_code == 200, done.text

    eligible = (
        await client.get(
            f"{API}/buyer/reviews/catalog-items/{item_id}/eligibility",
            headers=buyer_auth["headers"],
        )
    ).json()
    assert eligible["contract_id"] == contract_id
    assert eligible["target_actor_id"] == supplier_auth["actor_id"]

    review = await client.post(
        f"{API}/buyer/reviews/",
        headers=buyer_auth["headers"],
        json={
            "contract_id": contract_id,
            "target_actor_id": supplier_auth["actor_id"],
            "rating": 4,
            "comment": "Бумага плотная, привезли вовремя",
        },
    )
    assert review.status_code == 200, review.text
    assert review.json()["catalog_item_id"] == item_id

    page = (await client.get(f"{API}/public/catalog/items/{item_id}/reviews")).json()
    assert page["total"] == 1
    assert page["items"][0]["comment"] == "Бумага плотная, привезли вовремя"
    assert page["summary"] == {
        "average": 4.0,
        "count": 1,
        "distribution": {"5": 0, "4": 1, "3": 0, "2": 0, "1": 0},
    }

    after = (
        await client.get(
            f"{API}/buyer/reviews/catalog-items/{item_id}/eligibility",
            headers=buyer_auth["headers"],
        )
    ).json()
    assert after == {"contract_id": None, "target_actor_id": None, "already_reviewed": True}


@pytest.mark.asyncio
async def test_buyer_can_choose_to_pay_at_once_when_accepting_a_proposal(
    client, buyer_auth, supplier_auth, admin_auth
):
    item_id = await _active_item(client, supplier_auth, admin_auth, {})
    quote = (
        await client.post(
            f"{API}/buyer/catalog/items/{item_id}/offers",
            headers=buyer_auth["headers"],
            json={"kind": "quote", "qty": 10},
        )
    ).json()
    proposal = await client.post(
        f"{API}/supplier/rfqs/{quote['rfq_id']}/proposals",
        headers=supplier_auth["headers"],
        json={"price": 5000, "currency": "TJS", "delivery_time": "5 дней", "message": "Цена за 10"},
    )
    assert proposal.status_code == 200, proposal.text
    accepted = await client.post(
        f"{API}/buyer/proposals/{proposal.json()['id']}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": "full_postpayment", "payment_mode": "direct"},
    )
    assert accepted.status_code == 200, accepted.text
    contract_id = accepted.json()["contract_id"]
    row = await fetchrow(
        "SELECT payment_mode, payment_type::text AS payment_type FROM contracts WHERE id = $1",
        contract_id,
    )
    assert row["payment_mode"] == "direct"
    assert row["payment_type"] == "full_prepayment"
