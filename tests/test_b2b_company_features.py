"""B2B features: who speaks for the company, self-dealing by ИНН, request categories.

- chats and reviews carry the sender's company and role;
- two companies with one ИНН cannot trade, and a supplier does not see its own
  company's requests on the board;
- the board filters by category (a parent covers its subcategories, requests may
  store the category as an id or a slug);
- employees subscribe to categories and get notified about new public requests.
"""

import secrets

from app.modules.proposals.services.common import normalize_tax_number
from app.modules.rfqs.services.read import parse_category_ids
from tests.buyer.test_companies import _wizard_payload
from tests.sql import fetch, fetchval
from tests.test_audit_regressions import _actor, _headers, _register

API = "/api/v1"


def test_tax_number_normalization():
    assert normalize_tax_number(" 020-033 881 ") == "020033881"
    assert normalize_tax_number(None) == ""
    assert normalize_tax_number("") == ""


def test_parse_category_ids_ignores_junk():
    assert parse_category_ids("1, 4,x,4", None) == [1, 4]
    assert parse_category_ids(None, "7") == [7]
    assert parse_category_ids("", "") == []


async def _company(client, token, me, *, side: str, title: str, tax: str | None = None) -> tuple[int, dict]:
    actor = next(a["id"] for a in me["actors"] if a["side"] == side)
    payload = _wizard_payload(title)
    payload["actor_types"] = [side]
    if tax is not None:
        payload["tax_number"] = tax
    res = await client.post(f"{API}/{side}/companies/", headers=_headers(token, actor), json=payload)
    assert res.status_code == 200, res.text
    company_id = res.json()["id"]
    return company_id, _headers(token, await _actor(client, token, side=side, company_id=company_id))


async def _public_rfq(client, headers, title: str, category_id: str) -> str:
    res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=headers,
        json={
            "type": "service",
            "title": title,
            "category_id": category_id,
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "receiving_proposals",
            "project_duration": "2 months",
            "start_date": "2026-07-01",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def _category(slug: str) -> dict:
    rows = await fetch("SELECT id, parent_id, slug FROM categories WHERE slug = $1", slug)
    assert rows, f"category {slug} missing from the test database"
    return dict(rows[0])


async def test_same_tax_number_blocks_the_deal_and_hides_the_request(client):
    tax = f"{secrets.randbelow(10**9):09d}"
    _, b_token, b_me = await _register(client, "buyer")
    _, buyer = await _company(client, b_token, b_me, side="buyer", title="Twin buyer", tax=tax)
    _, s_token, s_me = await _register(client, "supplier")
    _, supplier = await _company(client, s_token, s_me, side="supplier", title="Twin supplier", tax=f" {tax[:3]}-{tax[3:]} ")

    rfq_id = await _public_rfq(client, buyer, f"Twin request {secrets.token_hex(3)}", "web")

    board = await client.get(f"{API}/supplier/rfqs/board?page_size=100", headers=supplier)
    assert board.status_code == 200, board.text
    assert rfq_id not in {item["id"] for item in board.json()["items"]}

    res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier,
        json={"price": 100, "currency": "TJS", "delivery_time": "1 day", "message": "twin"},
    )
    assert res.status_code == 409, res.text
    assert "ИНН" in res.text


async def test_board_category_filter_covers_subcategories_and_slugs(client):
    web = await _category("web")
    _, b_token, b_me = await _register(client, "buyer")
    _, buyer = await _company(client, b_token, b_me, side="buyer", title="Filter buyer")
    by_slug = await _public_rfq(client, buyer, f"Slug request {secrets.token_hex(3)}", "web")
    by_id = await _public_rfq(client, buyer, f"Id request {secrets.token_hex(3)}", str(web["id"]))
    other = await _public_rfq(client, buyer, f"Other request {secrets.token_hex(3)}", "logistics")

    _, s_token, s_me = await _register(client, "supplier")
    _, supplier = await _company(client, s_token, s_me, side="supplier", title="Filter supplier")
    res = await client.get(
        f"{API}/supplier/rfqs/board?page_size=100&category_ids={web['parent_id']}",
        headers=supplier,
    )
    assert res.status_code == 200, res.text
    ids = {item["id"] for item in res.json()["items"]}
    assert {by_slug, by_id} <= ids
    assert other not in ids


async def test_category_subscribers_are_notified_about_new_requests(client):
    web = await _category("web")
    _, s_token, s_me = await _register(client, "supplier")
    _, supplier = await _company(client, s_token, s_me, side="supplier", title="Alert supplier")

    res = await client.put(
        f"{API}/supplier/rfqs/subscriptions",
        headers=supplier,
        json={"category_ids": [web["parent_id"]]},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"category_ids": [web["parent_id"]]}
    res = await client.get(f"{API}/supplier/rfqs/subscriptions", headers=supplier)
    assert res.json() == {"category_ids": [web["parent_id"]]}
    res = await client.put(
        f"{API}/supplier/rfqs/subscriptions", headers=supplier, json={"category_ids": [987654]}
    )
    assert res.status_code in (400, 422), res.text

    _, b_token, b_me = await _register(client, "buyer")
    _, buyer = await _company(client, b_token, b_me, side="buyer", title="Alert buyer")
    title = f"Alert request {secrets.token_hex(3)}"
    rfq_id = await _public_rfq(client, buyer, title, "web")
    await _public_rfq(client, buyer, f"Quiet request {secrets.token_hex(3)}", "logistics")

    user_id = s_me["user"]["id"]
    rows = await fetch(
        "SELECT title, body, href FROM notifications WHERE user_id = $1 AND type::text = 'rfq'",
        user_id,
    )
    assert [r["href"] for r in rows] == [f"/supplier/rfqs/{rfq_id}"]
    assert title in rows[0]["body"]


async def test_chat_messages_carry_sender_company_and_role(client):
    _, b_token, b_me = await _register(client, "buyer")
    buyer_company, buyer = await _company(client, b_token, b_me, side="buyer", title="Chat buyer Co")
    _, s_token, s_me = await _register(client, "supplier")
    _, supplier = await _company(client, s_token, s_me, side="supplier", title="Chat supplier Co")
    supplier_actor = int(supplier["X-Actor-Id"])

    res = await client.post(
        f"{API}/buyer/direct-chats/", headers=buyer, json={"supplier_actor_id": supplier_actor}
    )
    assert res.status_code == 200, res.text
    chat_id = res.json()["id"]
    res = await client.post(
        f"{API}/buyer/direct-chats/{chat_id}/messages", headers=buyer, json={"text": "Здравствуйте"}
    )
    assert res.status_code == 200, res.text
    message = res.json()["messages"][-1]
    assert message["sender_company"] == "Chat buyer Co"
    assert message["sender_role"] == "director"
    assert await fetchval("SELECT title FROM companies WHERE id = $1", buyer_company) == "Chat buyer Co"


async def test_review_shows_the_employee_who_wrote_it(client):
    from tests.admin.test_disputes import _create_contract
    from tests.sql import execute

    _, b_token, b_me = await _register(client, "buyer")
    _, buyer = await _company(client, b_token, b_me, side="buyer", title="Review buyer Co")
    _, s_token, s_me = await _register(client, "supplier")
    supplier_company, supplier = await _company(
        client, s_token, s_me, side="supplier", title="Review supplier Co"
    )

    contract_id = await _create_contract(
        client, {"headers": buyer}, {"headers": supplier}, f"Review deal {secrets.token_hex(3)}"
    )
    await execute(
        "UPDATE contracts SET status = 'completed'::contract_status WHERE id = $1", contract_id
    )
    res = await client.post(
        f"{API}/buyer/reviews/",
        headers=buyer,
        json={
            "contract_id": contract_id,
            "target_actor_id": int(supplier["X-Actor-Id"]),
            "rating": 5,
            "comment": "Всё вовремя",
        },
    )
    assert res.status_code == 200, res.text
    author = res.json()["author"]
    assert author["user_id"] == b_me["user"]["id"]
    assert author["role"] == "director"
    assert author["company_title"] == "Review buyer Co"
    assert author["name"] == "Audit User"

    # Reputation is counted per role: this company was reviewed as a supplier.
    res = await client.get(f"{API}/public/companies/{supplier_company}")
    assert res.status_code == 200, res.text
    reputation = {r["side"]: r for r in res.json()["reputation"]}
    assert reputation["supplier"] == {
        "side": "supplier",
        "rating": 5.0,
        "reviews_count": 1,
        "completed_deals": 1,
    }


async def test_side_roles_close_sales_and_narrow_purchases(client):
    """A purchasing manager in a company that also sells: no access to sales."""
    _, d_token, d_me = await _register(client, "both")
    buyer_actor = next(a["id"] for a in d_me["actors"] if a["side"] == "buyer")
    payload = _wizard_payload("Two sided Co")
    payload["actor_types"] = ["buyer", "supplier"]
    res = await client.post(f"{API}/buyer/companies/", headers=_headers(d_token, buyer_actor), json=payload)
    assert res.status_code == 200, res.text
    company_id = res.json()["id"]
    director = _headers(d_token, await _actor(client, d_token, side="buyer", company_id=company_id))

    m_email, m_token, m_me = await _register(client, "both")
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": m_email, "role": "moderator"},
    )
    assert res.status_code == 200, res.text
    member_id = m_me["user"]["id"]
    sales_actor = await _actor(client, m_token, side="supplier", company_id=company_id)
    purchases_actor = await _actor(client, m_token, side="buyer", company_id=company_id)

    # Close sales for the member.
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{member_id}",
        headers=director,
        json={"supplier_role": "none"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["supplier_role"] == "none"
    assert res.json()["role"] == "moderator"

    me = (await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {m_token}"})).json()
    assert sales_actor not in {a["id"] for a in me["actors"]}
    assert purchases_actor in {a["id"] for a in me["actors"]}
    res = await client.get(f"{API}/supplier/rfqs/board", headers=_headers(m_token, sales_actor))
    assert res.status_code == 403, res.text

    # Purchases as accountant only: may not create requests.
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{member_id}",
        headers=director,
        json={"buyer_role": "accountant"},
    )
    assert res.status_code == 200, res.text
    rfq = {
        "type": "service",
        "title": "Side role request",
        "category_id": "web",
        "budget_type": "open",
        "currency": "TJS",
        "deadline": "2026-12-31",
        "visibility": "public",
        "status": "draft",
        "project_duration": "1 month",
        "start_date": "2026-07-01",
    }
    member_buyer = _headers(m_token, purchases_actor)
    res = await client.post(f"{API}/buyer/rfqs/", headers=member_buyer, json=rfq)
    assert res.status_code == 403, res.text

    # Back to the company role (moderator): requests are allowed again.
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{member_id}",
        headers=director,
        json={"buyer_role": None},
    )
    assert res.status_code == 200, res.text
    assert res.json()["buyer_role"] is None
    res = await client.post(f"{API}/buyer/rfqs/", headers=member_buyer, json=rfq)
    assert res.status_code == 200, res.text

    # The team list shows the overrides; the director keeps full access.
    res = await client.get(f"{API}/buyer/companies/{company_id}", headers=director)
    members = {m["user_id"]: m for m in res.json()["company_users"]}
    assert members[member_id]["supplier_role"] == "none"
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{d_me['user']['id']}",
        headers=director,
        json={"supplier_role": "none"},
    )
    assert res.status_code == 403, res.text
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{member_id}",
        headers=director,
        json={"supplier_role": "owner"},
    )
    assert res.status_code in (400, 422), res.text
