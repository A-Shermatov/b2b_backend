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
