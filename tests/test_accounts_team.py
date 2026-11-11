"""Who you are on the platform: company, private person, company employee.

- registration as a company, as a private person and from an invitation link;
- invitations: sent, accepted, declined, cancelled, sent again, expired;
- access levels: a viewer looks and changes nothing, anyone may leave the team;
- a removed employee is told so and loses access at once;
- the contact person next to the company name in requests and offers;
- the private person's page and the list of suppliers.
"""

import secrets

from tests.buyer.test_companies import _wizard_payload
from tests.sql import execute, fetchrow, fetchval
from tests.test_audit_regressions import _actor, _headers, _join_team, _register

API = "/api/v1"
PASSWORD = "Passw0rd!"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _email(prefix: str = "team") -> str:
    return f"{prefix}_{secrets.token_hex(5)}@example.com"


def _token_from(invite_url: str) -> str:
    return invite_url.rsplit("/invite/", 1)[1]


async def _register_as(client, **extra) -> tuple[str, str, dict]:
    email = extra.pop("email", None) or _email()
    body = {
        "email": email,
        "password": PASSWORD,
        "first_name": extra.pop("first_name", "Сотрудник"),
        "last_name": extra.pop("last_name", "Тестов"),
        "role": extra.pop("role", "buyer"),
        **extra,
    }
    res = await client.post(f"{API}/auth/register", json=body)
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    me = (await client.get(f"{API}/auth/me", headers=_bearer(token))).json()
    return email, token, me


async def _company(client, *, side: str = "buyer", title: str = "Team Co"):
    """A fresh company registered as a company: (company_id, director headers, token, me)."""
    _, token, me = await _register_as(
        client, role=side, account_type="company", company_title=f"{title} {secrets.token_hex(3)}"
    )
    company_id = me["companies"][0]["id"]
    headers = _headers(token, await _actor(client, token, side=side, company_id=company_id))
    return company_id, headers, token, me


def _rfq_body(title: str) -> dict:
    return {
        "type": "service",
        "title": title,
        "category_id": "it",
        "budget_type": "open",
        "currency": "TJS",
        "deadline": "2026-12-31",
        "visibility": "public",
        "status": "receiving_proposals",
        "project_duration": "2 months",
        "start_date": "2026-07-01",
    }


# ------------------------------------------------------------ registration


async def test_register_as_company_opens_the_company_cabinet(client):
    title = f"ООО Регистрация {secrets.token_hex(3)}"
    _, token, me = await _register_as(
        client, role="supplier", account_type="company", company_title=title
    )
    assert [c["title"] for c in me["companies"]] == [title]
    assert me["companies"][0]["role"] == "director"
    assert me["accepts_orders"] is False
    active = next(a for a in me["actors"] if a["id"] == me["active_actor_id"])
    assert active["kind"] == "company" and active["side"] == "supplier"

    # The company is listed among suppliers; its director as a private person is not.
    res = await client.get(f"{API}/public/suppliers", params={"q": title})
    assert [s["kind"] for s in res.json()["items"]] == ["company"]
    personal = next(a["id"] for a in me["actors"] if a["kind"] == "individual")
    listed = (await client.get(f"{API}/public/suppliers", params={"page_size": 100})).json()
    assert personal not in {s["actor_id"] for s in listed["items"]}


async def test_register_as_company_needs_a_name(client):
    res = await client.post(
        f"{API}/auth/register",
        json={
            "email": _email(),
            "password": PASSWORD,
            "first_name": "Без",
            "last_name": "Названия",
            "role": "buyer",
            "account_type": "company",
        },
    )
    assert res.status_code == 422, res.text


async def test_private_supplier_page_is_public(client):
    surname = f"Фрилансов{secrets.token_hex(3)}"
    _, token, me = await _register_as(
        client,
        role="supplier",
        account_type="individual",
        first_name="Рустам",
        last_name=surname,
        headline="Программист Python",
    )
    assert me["accepts_orders"] is True
    assert me["companies"] == []
    actor_id = next(a["id"] for a in me["actors"] if a["side"] == "supplier")

    page = (await client.get(f"{API}/auth/me/page", headers=_bearer(token))).json()
    assert page["headline"] == "Программист Python"
    assert page["supplier_actor_id"] == actor_id

    skill = f"Навык{secrets.token_hex(3)}"
    res = await client.put(
        f"{API}/auth/me/page",
        headers=_bearer(token),
        json={
            "headline": "Программист Python и FastAPI",
            "about": "  Делаю серверную часть сайтов.  ",
            "skills": ["Python", "python", skill, "  "],
            "city": "Душанбе",
            "website": "example.com/rustam",
            "rate_amount": 150,
            "rate_currency": "TJS",
            "rate_unit": "hour",
            "portfolio": [
                {"title": "Сайт магазина", "description": "Каталог и оплата", "link": "shop.example.com"}
            ],
            "accepts_orders": True,
        },
    )
    assert res.status_code == 200, res.text
    saved = res.json()
    assert saved["skills"] == ["Python", skill]
    assert saved["about"] == "Делаю серверную часть сайтов."
    assert saved["website"] == "https://example.com/rustam"

    public = (await client.get(f"{API}/public/suppliers/{actor_id}")).json()
    assert public["kind"] == "individual"
    assert public["headline"] == "Программист Python и FastAPI"
    assert public["description"] == "Делаю серверную часть сайтов."
    assert public["skills"] == ["Python", skill]
    assert public["city"] == "Душанбе"
    assert (public["rate_amount"], public["rate_currency"], public["rate_unit"]) == (150, "TJS", "hour")
    assert public["portfolio"][0]["title"] == "Сайт магазина"
    assert public["portfolio"][0]["link"] == "https://shop.example.com"

    # Found by a skill; gone from the list once the person stops taking orders,
    # while a direct link to the page keeps working.
    found = (await client.get(f"{API}/public/suppliers", params={"q": skill})).json()
    assert [s["actor_id"] for s in found["items"]] == [actor_id]
    res = await client.put(
        f"{API}/auth/me/page", headers=_bearer(token), json={**saved, "accepts_orders": False}
    )
    assert res.status_code == 200, res.text
    found = (await client.get(f"{API}/public/suppliers", params={"q": skill})).json()
    assert found["items"] == []
    assert (await client.get(f"{API}/public/suppliers/{actor_id}")).status_code == 200


async def test_page_refuses_foreign_pictures_and_buyer_cannot_take_orders(client):
    _, token, _ = await _register_as(client, role="buyer")
    res = await client.put(
        f"{API}/auth/me/page", headers=_bearer(token), json={"accepts_orders": True}
    )
    assert res.status_code == 422, res.text
    res = await client.put(
        f"{API}/auth/me/page",
        headers=_bearer(token),
        json={"portfolio": [{"title": "Работа", "image": "https://evil.example/x.png"}]},
    )
    assert res.status_code == 422, res.text


# ------------------------------------------------------------ invitations


async def test_invite_a_person_who_is_not_registered_yet(client):
    company_id, director, _, _ = await _company(client, side="supplier")
    email = _email("newcomer")
    res = await client.post(
        f"{API}/supplier/companies/{company_id}/team",
        headers=director,
        json={"email": email.upper(), "role": "moderator"},
    )
    assert res.status_code == 200, res.text
    invitation = res.json()
    assert invitation["status"] == "pending" and invitation["email"] == email
    token = _token_from(invitation["invite_url"])

    # Nobody joined yet: the team is the director alone, the invitation waits.
    company = (await client.get(f"{API}/supplier/companies/{company_id}", headers=director)).json()
    assert len(company["company_users"]) == 1
    waiting = (
        await client.get(f"{API}/supplier/companies/{company_id}/invitations", headers=director)
    ).json()
    assert [(i["email"], i["status"], i["invite_url"]) for i in waiting] == [(email, "pending", None)]

    preview = (await client.get(f"{API}/public/invitations/{token}")).json()
    assert preview["status"] == "pending"
    assert preview["email"] == email and preview["role"] == "moderator"
    assert preview["company_title"] == company["title"]

    # The link is for this address only.
    res = await client.post(
        f"{API}/auth/register",
        json={
            "email": _email("other"),
            "password": PASSWORD,
            "first_name": "Чужой",
            "last_name": "Адрес",
            "role": "buyer",
            "invite_token": token,
        },
    )
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "invitation_other_email"

    _, member_token, me = await _register_as(
        client, email=email, role="buyer", invite_token=token, first_name="Новый", last_name="Менеджер"
    )
    assert [(c["id"], c["role"]) for c in me["companies"]] == [(company_id, "moderator")]
    # The company sells, so the newcomer got the supplier side and starts in the company.
    assert me["capabilities"]["supplier"] is True
    assert me["accepts_orders"] is False
    active = next(a for a in me["actors"] if a["id"] == me["active_actor_id"])
    assert active["company_id"] == company_id

    # The link works once.
    res = await client.post(
        f"{API}/auth/invitations/accept", headers=_bearer(member_token), json={"token": token}
    )
    assert res.status_code == 409, res.text
    assert (await client.get(f"{API}/public/invitations/{token}")).json()["status"] == "accepted"

    # The director is told.
    notes = (await client.get(f"{API}/supplier/notifications/", headers=director)).json()["items"]
    assert any(n["title"] == "Приглашение принято" for n in notes)


async def test_registered_person_accepts_or_declines_in_the_cabinet(client):
    company_id, director, _, _ = await _company(client)
    email, token, _ = await _register(client, "buyer")

    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": email, "role": "accountant"},
    )
    assert res.status_code == 200, res.text
    invitation_id = res.json()["id"]

    # The same address cannot be invited twice while the first invitation waits.
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": email, "role": "viewer"},
    )
    assert res.status_code == 409, res.text

    mine = (await client.get(f"{API}/auth/invitations", headers=_bearer(token))).json()
    assert [(i["id"], i["role"]) for i in mine] == [(invitation_id, "accountant")]

    res = await client.post(
        f"{API}/auth/invitations/{invitation_id}/decline", headers=_bearer(token)
    )
    assert res.status_code == 204, res.text
    assert (await client.get(f"{API}/auth/invitations", headers=_bearer(token))).json() == []
    seen = (
        await client.get(f"{API}/buyer/companies/{company_id}/invitations", headers=director)
    ).json()
    assert [(i["email"], i["status"]) for i in seen] == [(email, "declined")]

    # A declined person can be invited again, and a member cannot be invited at all.
    await _join_team(client, director, company_id, email, "viewer", token)
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": email, "role": "moderator"},
    )
    assert res.status_code == 409, res.text
    assert "уже в команде" in res.json()["detail"]


async def test_cancel_resend_and_expiry(client):
    company_id, director, _, _ = await _company(client)
    email, token, _ = await _register(client, "buyer")
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": email, "role": "moderator"},
    )
    first = res.json()
    old_link = _token_from(first["invite_url"])

    # "Send again" makes a new link; the old one stops working.
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/invitations/{first['id']}/resend", headers=director
    )
    assert res.status_code == 200, res.text
    new_link = _token_from(res.json()["invite_url"])
    assert new_link != old_link
    assert (await client.get(f"{API}/public/invitations/{old_link}")).status_code == 404
    assert (await client.get(f"{API}/public/invitations/{new_link}")).json()["status"] == "pending"

    # Past its date: shown as expired, cannot be accepted, and inviting again reopens it.
    await execute(
        "UPDATE company_invitations SET expires_at = NOW() - INTERVAL '1 day' WHERE id = $1",
        first["id"],
    )
    assert (await client.get(f"{API}/public/invitations/{new_link}")).json()["status"] == "expired"
    assert (await client.get(f"{API}/auth/invitations", headers=_bearer(token))).json() == []
    res = await client.post(
        f"{API}/auth/invitations/accept", headers=_bearer(token), json={"token": new_link}
    )
    assert res.status_code == 409, res.text
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": email, "role": "moderator"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == first["id"] and res.json()["status"] == "pending"
    fresh_link = _token_from(res.json()["invite_url"])

    # Cancelled by the director: the link is dead.
    res = await client.delete(
        f"{API}/buyer/companies/{company_id}/invitations/{first['id']}", headers=director
    )
    assert res.status_code == 204, res.text
    res = await client.post(
        f"{API}/auth/invitations/accept", headers=_bearer(token), json={"token": fresh_link}
    )
    assert res.status_code == 409, res.text
    assert (await client.get(f"{API}/auth/me", headers=_bearer(token))).json()["companies"] == []


async def test_address_alone_does_not_let_a_stranger_in(client):
    """Emails are not confirmed: registering the invited address later is not enough."""
    company_id, director, _, _ = await _company(client)
    email = _email("victim")
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": email, "role": "admin"},
    )
    invitation_id = res.json()["id"]

    _, stranger, _ = await _register_as(client, email=email)
    assert (await client.get(f"{API}/auth/invitations", headers=_bearer(stranger))).json() == []
    res = await client.post(
        f"{API}/auth/invitations/{invitation_id}/accept", headers=_bearer(stranger)
    )
    assert res.status_code == 404, res.text

    # Only managers see and manage invitations; a wrong link gives nothing away.
    _, outsider, me = await _register(client, "buyer")
    outsider_headers = _headers(outsider, me["actors"][0]["id"])
    res = await client.get(
        f"{API}/buyer/companies/{company_id}/invitations", headers=outsider_headers
    )
    assert res.status_code == 403, res.text
    assert (await client.get(f"{API}/public/invitations/{'x' * 43}")).status_code == 404


async def test_company_form_invites_colleagues_instead_of_adding_them(client):
    colleague, colleague_token, _ = await _register(client, "buyer")
    _, token, me = await _register(client, "buyer")
    payload = _wizard_payload("Wizard Team Co")
    payload["team"] = [{"email": colleague, "role": "moderator"}]
    res = await client.post(
        f"{API}/buyer/companies/",
        headers=_headers(token, me["actors"][0]["id"]),
        json=payload,
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["company_users"]) == 1
    mine = (await client.get(f"{API}/auth/invitations", headers=_bearer(colleague_token))).json()
    assert [i["company_id"] for i in mine] == [res.json()["id"]]


# ------------------------------------------------------------ access levels


async def test_viewer_sees_everything_and_changes_nothing(client):
    company_id, director, _, _ = await _company(client)
    email, token, _ = await _register(client, "buyer")
    await _join_team(client, director, company_id, email, "viewer", token)
    viewer = _headers(token, await _actor(client, token, side="buyer", company_id=company_id))

    res = await client.post(f"{API}/buyer/rfqs/", headers=director, json=_rfq_body("Viewer RFQ"))
    assert res.status_code == 200, res.text

    assert (await client.get(f"{API}/buyer/rfqs/", headers=viewer)).status_code == 200
    assert (await client.get(f"{API}/buyer/companies/{company_id}", headers=viewer)).status_code == 200

    res = await client.post(f"{API}/buyer/rfqs/", headers=viewer, json=_rfq_body("Not allowed"))
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "company_role_forbidden"
    assert "только для просмотра" in res.json()["detail"]
    for method, path, body in (
        ("post", "/buyer/direct-chats/", {"supplier_actor_id": 1}),
        ("post", f"/buyer/companies/{company_id}/team", {"email": _email(), "role": "viewer"}),
        ("patch", f"/buyer/companies/{company_id}", {"description": "changed"}),
        ("post", "/buyer/companies/logo", None),
    ):
        res = await getattr(client, method)(f"{API}{path}", headers=viewer, json=body)
        assert res.status_code == 403, (path, res.text)

    # What a person does for themselves stays open: read marks and leaving the team.
    assert (await client.post(f"{API}/buyer/notifications/read-all", headers=viewer)).status_code < 300
    me = (await client.get(f"{API}/auth/me", headers=_bearer(token))).json()
    res = await client.delete(
        f"{API}/buyer/companies/{company_id}/team/{me['user']['id']}", headers=viewer
    )
    assert res.status_code == 204, res.text
    assert (await client.get(f"{API}/auth/me", headers=_bearer(token))).json()["companies"] == []


async def test_viewer_role_on_one_side(client):
    _, token, me = await _register(client, "both")
    payload = _wizard_payload("Two sided Co")
    payload["actor_types"] = ["buyer", "supplier"]
    res = await client.post(
        f"{API}/buyer/companies/", headers=_headers(token, me["actors"][0]["id"]), json=payload
    )
    company_id = res.json()["id"]
    director = _headers(token, await _actor(client, token, side="buyer", company_id=company_id))
    email, m_token, m_me = await _register(client, "both")
    await _join_team(client, director, company_id, email, "moderator", m_token)

    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{m_me['user']['id']}",
        headers=director,
        json={"buyer_role": "viewer"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["buyer_role"] == "viewer"
    purchases = _headers(m_token, await _actor(client, m_token, side="buyer", company_id=company_id))
    res = await client.post(f"{API}/buyer/rfqs/", headers=purchases, json=_rfq_body("Side viewer"))
    assert res.status_code == 403, res.text
    assert (await client.get(f"{API}/buyer/rfqs/", headers=purchases)).status_code == 200


async def test_manager_can_leave_and_director_cannot_be_removed(client):
    company_id, director, d_token, d_me = await _company(client)
    email, token, me = await _register(client, "buyer")
    await _join_team(client, director, company_id, email, "moderator", token)
    manager = _headers(token, await _actor(client, token, side="buyer", company_id=company_id))

    res = await client.delete(
        f"{API}/buyer/companies/{company_id}/team/{d_me['user']['id']}", headers=manager
    )
    assert res.status_code == 403, res.text
    res = await client.delete(
        f"{API}/buyer/companies/{company_id}/team/{d_me['user']['id']}", headers=director
    )
    assert res.status_code == 403, res.text
    assert "поддержку" in res.json()["detail"]

    res = await client.delete(
        f"{API}/buyer/companies/{company_id}/team/{me['user']['id']}", headers=manager
    )
    assert res.status_code == 204, res.text


async def test_removed_employee_is_told_and_loses_access_at_once(client):
    company_id, director, _, _ = await _company(client)
    email, token, me = await _register(client, "buyer")
    await _join_team(client, director, company_id, email, "moderator", token)
    member = _headers(token, await _actor(client, token, side="buyer", company_id=company_id))
    res = await client.post(f"{API}/buyer/rfqs/", headers=member, json=_rfq_body("Before removal"))
    assert res.status_code == 200, res.text
    rfq_id = res.json()["id"]
    assert res.json()["contact_role"] == "moderator"

    res = await client.delete(
        f"{API}/buyer/companies/{company_id}/team/{me['user']['id']}", headers=director
    )
    assert res.status_code == 204, res.text

    res = await client.get(f"{API}/buyer/rfqs/", headers=member)
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "company_access_revoked"
    assert "больше нет доступа" in res.json()["detail"]

    # Their personal profile still works, and it has the notice.
    personal = _headers(token, await _actor(client, token, side="buyer"))
    notes = (await client.get(f"{API}/buyer/notifications/", headers=personal)).json()["items"]
    assert any("закрыт" in n["title"] for n in notes)

    # The request stays with the company; its author is shown without a role now.
    res = await client.get(f"{API}/buyer/rfqs/{rfq_id}", headers=director)
    assert res.status_code == 200, res.text
    assert res.json()["created_by"] == "Audit User"
    assert res.json()["contact_role"] is None


# ------------------------------------------------------------ who the other side sees


async def test_offer_shows_the_company_and_its_contact_person(client):
    buyer_company, buyer_director, _, _ = await _company(client, title="Buyer Co")
    supplier_company, supplier_director, _, _ = await _company(
        client, side="supplier", title="Supplier Co"
    )
    email, token, _ = await _register_as(
        client, role="supplier", first_name="Алишер", last_name="Продавцов"
    )
    await _join_team(
        client, supplier_director, supplier_company, email, "moderator", token, side="supplier"
    )
    seller = _headers(
        token, await _actor(client, token, side="supplier", company_id=supplier_company)
    )

    res = await client.post(
        f"{API}/buyer/rfqs/", headers=buyer_director, json=_rfq_body("Contact person RFQ")
    )
    assert res.status_code == 200, res.text
    rfq_id = res.json()["id"]
    assert res.json()["contact_role"] == "director"

    res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=seller,
        json={"price": 500, "currency": "TJS", "delivery_time": "3 дня", "message": "Сделаем"},
    )
    assert res.status_code == 200, res.text
    proposal_id = res.json()["id"]
    author = await fetchval("SELECT author_user_id FROM proposals WHERE id = $1", proposal_id)
    assert author is not None

    res = await client.get(f"{API}/buyer/rfqs/{rfq_id}/proposals", headers=buyer_director)
    assert res.status_code == 200, res.text
    items = res.json()["items"] if isinstance(res.json(), dict) else res.json()
    actor = next(p for p in items if p["id"] == proposal_id)["actor"]
    assert actor["kind"] == "company"
    assert actor["name"].startswith("Supplier Co")
    assert (actor["contact_name"], actor["contact_role"]) == ("Алишер Продавцов", "moderator")

    # After the employee leaves, the offer stays with the company; the name stays, the role goes.
    member_id = await fetchval("SELECT id FROM users WHERE email = $1", email)
    res = await client.delete(
        f"{API}/supplier/companies/{supplier_company}/team/{member_id}", headers=supplier_director
    )
    assert res.status_code == 204, res.text
    res = await client.get(f"{API}/buyer/rfqs/{rfq_id}/proposals", headers=buyer_director)
    items = res.json()["items"] if isinstance(res.json(), dict) else res.json()
    actor = next(p for p in items if p["id"] == proposal_id)["actor"]
    assert (actor["contact_name"], actor["contact_role"]) == ("Алишер Продавцов", None)


async def test_company_cover_is_shown_on_its_public_page(client):
    company_id, director, _, _ = await _company(client, side="supplier")
    res = await client.patch(
        f"{API}/supplier/companies/{company_id}",
        headers=director,
        json={"cover": "https://evil.example/cover.png"},
    )
    assert res.status_code == 422, res.text
    res = await client.patch(
        f"{API}/supplier/companies/{company_id}",
        headers=director,
        json={"cover": "logos/1/cover.png", "description": "О компании"},
    )
    assert res.status_code == 200, res.text
    public = (await client.get(f"{API}/public/companies/{company_id}")).json()
    assert public["cover"].endswith("logos/1/cover.png")
    row = await fetchrow("SELECT cover FROM companies WHERE id = $1", company_id)
    assert row["cover"] == "logos/1/cover.png"
