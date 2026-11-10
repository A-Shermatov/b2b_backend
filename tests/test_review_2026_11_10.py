"""Regression tests for the review of 2026-11-10 (findings N-xx)."""

import pytest

from tests.buyer.test_catalog_offers import _create_active_item
from tests.buyer.test_companies import _wizard_payload
from tests.test_audit_regressions import API, _actor, _headers, _register


async def _company_with_member(client, member_role: str):
    """A fresh buyer company: (company_id, director headers, member headers, member user id)."""
    _, director_token, director_me = await _register(client, "buyer")
    director = _headers(
        director_token, next(a["id"] for a in director_me["actors"] if a["side"] == "buyer")
    )
    res = await client.post(
        f"{API}/buyer/companies/", headers=director, json=_wizard_payload("Review N Co")
    )
    assert res.status_code == 200, res.text
    company_id = res.json()["id"]
    director = _headers(
        director_token, await _actor(client, director_token, side="buyer", company_id=company_id)
    )
    member_email, member_token, member_me = await _register(client, "buyer")
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=director,
        json={"email": member_email, "role": member_role},
    )
    assert res.status_code == 200, res.text
    member = _headers(
        member_token, await _actor(client, member_token, side="buyer", company_id=company_id)
    )
    return company_id, director, member, member_me["user"]["id"]


# ---------------------------------------------------------------- N-01


@pytest.mark.asyncio
async def test_moderator_cannot_commit_company_with_negotiate_offer(
    client, supplier_auth, admin_auth
):
    item_id = await _create_active_item(client, supplier_auth, admin_auth)
    _, director, moderator, _ = await _company_with_member(client, "moderator")

    res = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=moderator,
        json={"kind": "negotiate", "price": 100000, "message": "Беру дороже", "payment_mode": "direct"},
    )
    assert res.status_code == 403, res.text

    # Asking for a quote stays open to the moderator; the director may still negotiate.
    res = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=moderator,
        json={"kind": "quote", "qty": 1},
    )
    assert res.status_code == 200, res.text
    res = await client.post(
        f"{API}/buyer/catalog/items/{item_id}/offers",
        headers=director,
        json={"kind": "negotiate", "price": 900, "message": "Торг от директора"},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------- N-04


@pytest.mark.asyncio
async def test_admin_cannot_lift_own_side_restriction_or_touch_admins(client):
    company_id, director, admin, admin_user_id = await _company_with_member(client, "admin")
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{admin_user_id}",
        headers=director,
        json={"supplier_role": "none"},
    )
    assert res.status_code == 200, res.text

    # The restricted admin cannot open the side again for himself.
    for body in ({"supplier_role": None}, {"supplier_role": "admin"}, {"role": "moderator"}):
        res = await client.patch(
            f"{API}/buyer/companies/{company_id}/team/{admin_user_id}", headers=admin, json=body
        )
        assert res.status_code == 403, (body, res.text)

    # Nor make, change or remove other admins.
    other_email, _, other_me = await _register(client, "buyer")
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=admin,
        json={"email": other_email, "role": "admin"},
    )
    assert res.status_code == 403, res.text
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=admin,
        json={"email": other_email, "role": "moderator"},
    )
    assert res.status_code == 200, res.text
    other_id = other_me["user"]["id"]
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{other_id}",
        headers=admin,
        json={"buyer_role": "admin"},
    )
    assert res.status_code == 403, res.text
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{other_id}",
        headers=admin,
        json={"buyer_role": "accountant"},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------- N-06


@pytest.mark.asyncio
async def test_company_logo_must_be_our_upload(client):
    company_id, director, _, _ = await _company_with_member(client, "moderator")
    for logo in ("http://169.254.169.254/latest/meta-data/", "http://minio:9000@evil.example/x", "../../app/.env"):
        res = await client.patch(
            f"{API}/buyer/companies/{company_id}", headers=director, json={"logo": logo}
        )
        assert res.status_code == 422, (logo, res.text)
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}", headers=director, json={"logo": "logos/1/x.png"}
    )
    assert res.status_code == 200, res.text


def test_telegram_logo_never_leaves_own_storage(monkeypatch):
    from app.modules.telegram import service as telegram

    def no_network(*_args, **_kwargs):
        raise AssertionError("must not fetch a foreign address")

    monkeypatch.setattr(telegram.httpx, "Client", no_network)
    assert telegram._logo_bytes("http://169.254.169.254/latest/meta-data/") is None
    assert telegram._logo_bytes("../../etc/passwd") is None
