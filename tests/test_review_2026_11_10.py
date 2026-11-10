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


# ---------------------------------------------------------------- H-07


@pytest.mark.asyncio
async def test_totp_code_cannot_be_replayed_at_login(client):
    import pyotp

    email, token, _ = await _register(client, "buyer")
    headers = {"Authorization": f"Bearer {token}"}
    secret = (await client.post(f"{API}/auth/2fa/setup", headers=headers)).json()["secret"]
    res = await client.post(
        f"{API}/auth/2fa/confirm", headers=headers, json={"code": pyotp.TOTP(secret).now()}
    )
    assert res.status_code == 200, res.text

    async def challenge():
        res = await client.post(f"{API}/auth/login", json={"email": email, "password": "Passw0rd!"})
        assert res.status_code == 200, res.text
        return res.json()["challenge_token"]

    code = pyotp.TOTP(secret).now()
    first = await client.post(f"{API}/auth/2fa/verify", json={"challenge_token": await challenge(), "code": code})
    assert first.status_code == 200, first.text
    again = await client.post(f"{API}/auth/2fa/verify", json={"challenge_token": await challenge(), "code": code})
    assert again.status_code == 422, again.text


# ---------------------------------------------------------------- M-15


@pytest.mark.asyncio
async def test_refresh_token_lives_in_an_httponly_cookie(client):
    email, _, _ = await _register(client, "buyer")
    res = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "Passw0rd!"},
        headers={"X-Refresh-Cookie": "1"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["refresh_token"] in ("", None)
    cookie = res.headers.get("set-cookie", "")
    assert "bm_refresh=" in cookie and "HttpOnly" in cookie and "/api/v1/auth" in cookie

    # The cookie alone refreshes; the new refresh token again stays out of the body.
    refreshed = await client.post(f"{API}/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]
    assert refreshed.json()["refresh_token"] in ("", None)

    out = await client.post(f"{API}/auth/logout")
    assert out.status_code == 204, out.text
    assert 'bm_refresh=""' in out.headers.get("set-cookie", "") or "Max-Age=0" in out.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_body_refresh_token_still_works_for_other_clients(client):
    email, _, _ = await _register(client, "buyer")
    res = await client.post(f"{API}/auth/login", json={"email": email, "password": "Passw0rd!"})
    refresh_token = res.json()["refresh_token"]
    assert refresh_token
    res = await client.post(f"{API}/auth/refresh", json={"refresh_token": refresh_token})
    assert res.status_code == 200, res.text
    assert res.json()["refresh_token"]


async def test_private_files_need_a_signed_link(client, tmp_path, monkeypatch):
    """H-02: identity/deal/dispute files are not public; links are signed and expire."""
    from urllib.parse import urlsplit

    from app.utils import storage

    monkeypatch.setattr(storage.settings, "local_upload_dir", str(tmp_path))
    monkeypatch.setattr(storage.storage_service, "_get_s3", lambda: None)
    (tmp_path / "identity" / "7").mkdir(parents=True)
    (tmp_path / "identity" / "7" / "passport.pdf").write_bytes(b"%PDF-1.4 secret")
    (tmp_path / "logos" / "7").mkdir(parents=True)
    (tmp_path / "logos" / "7" / "logo.png").write_bytes(b"png")

    url = storage.public_file_url("identity/7/passport.pdf")
    parts = urlsplit(url)
    assert parts.path == f"{API}/files/identity/7/passport.pdf"
    assert "sig=" in parts.query and "exp=" in parts.query
    # Signed links still map back to the stored key (e.g. when echoed by the client).
    assert storage.storage_key_from_url(url) == "identity/7/passport.pdf"
    assert storage.public_file_url(url).startswith(url.split("?")[0])
    assert "sig=" not in storage.public_file_url("logos/7/logo.png")

    ok = await client.get(f"{parts.path}?{parts.query}")
    assert ok.status_code == 200
    assert ok.content == b"%PDF-1.4 secret"
    assert "sandbox" in ok.headers["content-security-policy"]

    assert (await client.get(parts.path)).status_code == 404
    tampered = parts.query.replace("sig=", "sig=0")
    assert (await client.get(f"{parts.path}?{tampered}")).status_code == 404
    other = f"{API}/files/identity/8/passport.pdf?{parts.query}"
    assert (await client.get(other)).status_code == 404
    public = f"{API}/files/logos/7/logo.png?{parts.query}"
    assert (await client.get(public)).status_code == 404

    expired = storage.signed_file_url("identity/7/passport.pdf", ttl_seconds=-10)
    exp_parts = urlsplit(expired)
    assert (await client.get(f"{exp_parts.path}?{exp_parts.query}")).status_code == 404


@pytest.mark.asyncio
async def test_added_team_member_is_notified(client):
    """M-01: the person added to a team learns about it right away."""
    _, _, member, _ = await _company_with_member(client, "moderator")
    rows = await client.get(f"{API}/buyer/notifications/", headers=member)
    assert rows.status_code == 200
    body = rows.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    assert any("команду" in (n.get("title") or "") for n in items)
