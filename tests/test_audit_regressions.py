"""Regression tests for the pre-production audit (REVIEW.md).

Each test reproduces a finding and asserts the fixed behaviour. Alif is replaced by
an in-memory fake; no network calls are made.
"""

import asyncio
import secrets
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from tests.admin.test_disputes import _create_contract, _create_draft_rfq, _open_dispute
from tests.buyer.test_companies import _wizard_payload
from tests.sql import execute, fetch, fetchval

API = "/api/v1"


# --------------------------------------------------------------------------- helpers


async def _register(client, role="both"):
    email = f"audit_{secrets.token_hex(5)}@example.com"
    res = await client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "Passw0rd!",
            "first_name": "Audit",
            "last_name": "User",
            "role": role,
        },
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    me = (await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    return email, token, me


async def _actor(client, token, *, side, company_id=None):
    me = (await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    for actor in me["actors"]:
        if actor["side"] != side:
            continue
        if company_id is None and actor["kind"] == "individual":
            return actor["id"]
        if company_id is not None and actor.get("company_id") == company_id:
            return actor["id"]
    raise AssertionError(f"actor not found: side={side} company={company_id}")


def _headers(token, actor_id):
    return {"Authorization": f"Bearer {token}", "X-Actor-Id": str(actor_id)}


def _marketplace_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://unused",
        payment_provider="alif",
        alif_terminal_key="k",
        alif_terminal_password="p",
        alif_test_merchant_1="610184",
        alif_test_merchant_2="841418",
    )


class _FakeAlif:
    confirmed: list = []
    canceled: list = []

    def __init__(self, settings=None):
        pass

    def verify_callback(self, payload):
        return True

    async def create_payment(self, *, order_id, amount, callback_url, return_url, info=None,
                             mp_terminal_info=None, **_):
        await asyncio.sleep(0.05)  # network latency: lets a second click race the first
        return f"https://pay.example/{order_id}"

    async def confirm_delivery(self, *, transaction_id, amount, order_id=None):
        _FakeAlif.confirmed.append((transaction_id, Decimal(str(amount))))
        return {}

    async def cancel_marketplace(self, *, transaction_id, amount, reason="", order_id=None):
        _FakeAlif.canceled.append(transaction_id)
        return {}

    async def check_transaction(self, order_id):
        return None


@pytest.fixture
def fake_alif(monkeypatch):
    settings = _marketplace_settings()
    for module in (
        "app.modules.payments.services.gateway",
        "app.modules.payments.services.update",
        "app.modules.payments.api.callbacks",
        "app.modules.payments.api.read",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.payments.services.gateway.AlifClient", _FakeAlif)
    _FakeAlif.confirmed, _FakeAlif.canceled = [], []
    return _FakeAlif


async def _hold_callback(client, order_id, sub_id):
    res = await client.post(
        f"{API}/payments/alif/callback",
        json={
            "orderId": order_id,
            "status": "to_approve",
            "transactionId": f"t-{order_id}",
            "token": "x" * 64,
            "sub_transactions": [{"transaction_id": sub_id, "terminal_id": "610184"}],
        },
    )
    assert res.status_code == 200, res.text


async def _submit_and_accept(client, buyer_auth, supplier_auth, contract_id):
    res = await client.post(
        f"{API}/supplier/contracts/{contract_id}/submissions",
        headers=supplier_auth["headers"],
        json={"note": "done", "assets": []},
    )
    assert res.status_code == 200, res.text
    submission_id = res.json()["submissions"][-1]["id"]
    return await client.post(
        f"{API}/buyer/contracts/{contract_id}/submissions/{submission_id}/approve",
        headers=buyer_auth["headers"],
    )


# ------------------------------------------------------------------ C-01 double pay


async def test_second_paid_order_is_refunded_not_paid_out(client, buyer_auth, supplier_auth, fake_alif):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "Audit double pay")
    milestone_id = await fetchval(
        "SELECT id FROM payment_milestones WHERE contract_id = $1 ORDER BY id LIMIT 1", contract_id
    )
    first = await client.post(f"{API}/buyer/payments/milestones/{milestone_id}/fund", headers=buyer_auth["headers"])
    assert first.status_code == 200, first.text
    order_a = first.json()["order_id"]
    # The buyer comes back after the 30-minute reuse window: a new order is created.
    await execute(
        "UPDATE payment_gateway_transactions SET created_at = NOW() - INTERVAL '31 minutes' WHERE order_id = $1",
        order_a,
    )
    second = await client.post(f"{API}/buyer/payments/milestones/{milestone_id}/fund", headers=buyer_auth["headers"])
    order_b = second.json()["order_id"]
    assert order_b != order_a

    await _hold_callback(client, order_a, "sub-A")
    await _hold_callback(client, order_b, "sub-B")  # both links paid
    assert fake_alif.canceled == ["sub-B"], "the duplicate hold must be returned to the buyer"

    res = await _submit_and_accept(client, buyer_auth, supplier_auth, contract_id)
    assert res.status_code == 200, res.text
    assert [sub for sub, _ in fake_alif.confirmed] == ["sub-A"], "only one payout per milestone"


async def test_double_click_fund_creates_one_order(client, buyer_auth, supplier_auth, fake_alif):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "Audit double click")
    milestone_id = await fetchval(
        "SELECT id FROM payment_milestones WHERE contract_id = $1 ORDER BY id LIMIT 1", contract_id
    )
    responses = await asyncio.gather(
        *[
            client.post(f"{API}/buyer/payments/milestones/{milestone_id}/fund", headers=buyer_auth["headers"])
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    orders = await fetchval(
        "SELECT COUNT(*) FROM payment_gateway_transactions WHERE milestone_id = $1", milestone_id
    )
    assert orders == 1


# --------------------------------------------------------- H-01 / H-08 admin actions


async def test_frozen_deal_cannot_be_accepted_until_unfrozen(client, buyer_auth, supplier_auth, admin_auth):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "Audit freeze")
    milestone_id = await fetchval("SELECT id FROM payment_milestones WHERE contract_id = $1", contract_id)
    res = await client.post(f"{API}/buyer/payments/milestones/{milestone_id}/fund", headers=buyer_auth["headers"])
    assert res.status_code == 200, res.text
    res = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        headers=admin_auth["headers"],
        json={"action": "freeze", "reason": "проверка"},
    )
    assert res.status_code == 200, res.text

    res = await _submit_and_accept(client, buyer_auth, supplier_auth, contract_id)
    assert res.status_code == 409, res.text
    assert await fetchval("SELECT status::text FROM contracts WHERE id = $1", contract_id) != "completed"

    res = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        headers=admin_auth["headers"],
        json={"action": "unfreeze", "reason": "проверка завершена"},
    )
    assert res.status_code == 200, res.text
    submission_id = await fetchval(
        "SELECT id FROM work_submissions WHERE contract_id = $1 ORDER BY id DESC LIMIT 1", contract_id
    )
    res = await client.post(
        f"{API}/buyer/contracts/{contract_id}/submissions/{submission_id}/approve",
        headers=buyer_auth["headers"],
    )
    assert res.status_code == 200, res.text


async def test_force_complete_refused_while_dispute_open(client, buyer_auth, supplier_auth, admin_auth):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "Audit force complete")
    await _open_dispute(client, buyer_auth, contract_id, "Поставщик не выходит на связь уже неделю")
    res = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        headers=admin_auth["headers"],
        json={"action": "force_complete", "reason": "решение"},
    )
    assert res.status_code == 409, res.text
    assert await fetchval("SELECT status::text FROM contracts WHERE id = $1", contract_id) == "disputed"


# ------------------------------------------------- C-03 company roles, M-03, M-02, H-05


async def test_accountant_cannot_manage_company_or_commit_deals(client):
    # A fresh director: do not pile companies onto the shared demo accounts.
    _, director_token, director_me = await _register(client, "buyer")
    director = _headers(director_token, next(a["id"] for a in director_me["actors"] if a["side"] == "buyer"))
    buyer_auth = {"headers": director}
    res = await client.post(f"{API}/buyer/companies/", headers=buyer_auth["headers"], json=_wizard_payload("Audit RBAC Co"))
    assert res.status_code == 200, res.text
    company_id = res.json()["id"]
    acc_email, acc_token, acc_me = await _register(client, "buyer")
    res = await client.post(
        f"{API}/buyer/companies/{company_id}/team",
        headers=buyer_auth["headers"],
        json={"email": acc_email, "role": "accountant"},
    )
    assert res.status_code == 200, res.text
    acc = _headers(acc_token, await _actor(client, acc_token, side="buyer", company_id=company_id))

    other_email, _, other_me = await _register(client, "buyer")
    res = await client.post(f"{API}/buyer/companies/{company_id}/team", headers=acc, json={"email": other_email, "role": "admin"})
    assert res.status_code == 403, res.text
    res = await client.patch(f"{API}/buyer/companies/{company_id}", headers=acc, json={"description": "changed by accountant"})
    assert res.status_code == 403, res.text
    res = await client.post(f"{API}/buyer/proposals/999999/accept", headers=acc, json={"payment_type": "full_prepayment"})
    assert res.status_code == 403, res.text
    res = await client.post(f"{API}/buyer/payments/milestones/999999/approve", headers=acc)
    assert res.status_code == 403, res.text

    # M-03: the director can change a member's role (used to fail with 500).
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}/team/{acc_me['user']['id']}",
        headers=buyer_auth["headers"],
        json={"role": "moderator"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "moderator"


async def test_supplier_company_limit_applies_to_buyer_route(client):
    _, token, me = await _register(client, "both")
    buyer = _headers(token, next(a["id"] for a in me["actors"] if a["side"] == "buyer"))
    supplier = _headers(token, next(a["id"] for a in me["actors"] if a["side"] == "supplier"))
    first = _wizard_payload("Audit limit 1")
    first["actor_types"] = ["supplier"]
    assert (await client.post(f"{API}/supplier/companies/", headers=supplier, json=first)).status_code == 200
    second = _wizard_payload("Audit limit 2")
    second["actor_types"] = ["buyer", "supplier"]
    res = await client.post(f"{API}/buyer/companies/", headers=buyer, json=second)
    assert res.status_code == 409, res.text
    buyer_only = _wizard_payload("Audit limit buyer only")
    res = await client.post(f"{API}/buyer/companies/", headers=buyer, json=buyer_only)
    assert res.status_code == 200, res.text


async def test_member_of_supplier_company_cannot_deal_with_himself(client):
    a_email, a_token, a_me = await _register(client, "both")
    _, b_token, b_me = await _register(client, "supplier")
    b_sup = _headers(b_token, next(x["id"] for x in b_me["actors"] if x["side"] == "supplier"))
    payload = _wizard_payload("Audit puppet supplier")
    payload["actor_types"] = ["supplier"]
    res = await client.post(f"{API}/supplier/companies/", headers=b_sup, json=payload)
    assert res.status_code == 200, res.text
    company_id = res.json()["id"]
    res = await client.post(f"{API}/supplier/companies/{company_id}/team", headers=b_sup, json={"email": a_email, "role": "admin"})
    assert res.status_code == 200, res.text

    a_buyer = _headers(a_token, await _actor(client, a_token, side="buyer"))
    a_supplier = _headers(a_token, await _actor(client, a_token, side="supplier", company_id=company_id))
    rfq_id = await _create_draft_rfq(client, {"headers": a_buyer}, "Audit self deal")
    assert (await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=a_buyer)).status_code == 200
    res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=a_supplier,
        json={"price": 100, "currency": "TJS", "delivery_time": "1 day", "message": "self deal"},
    )
    if res.status_code == 200:
        res = await client.post(
            f"{API}/buyer/proposals/{res.json()['id']}/accept",
            headers=a_buyer,
            json={"payment_type": "full_prepayment"},
        )
    assert res.status_code == 409, res.text


# ------------------------------------------------------------ C-04 / H-03 / H-07 auth


def _request(peer, headers):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


def test_client_ip_ignores_spoofed_forwarded_for():
    from app.utils.request_meta import client_ip

    # Direct hit from the internet: the client cannot choose its IP.
    assert client_ip(_request("93.184.216.34", {"X-Forwarded-For": "1.1.1.1"})) == "93.184.216.34"
    # Through our proxy: the right-most public hop (appended by the proxy) wins.
    assert client_ip(_request("172.18.0.5", {"X-Forwarded-For": "1.1.1.1, 8.8.4.4"})) == "8.8.4.4"
    # Cloudflare header has priority.
    assert client_ip(_request("172.18.0.5", {"CF-Connecting-IP": "9.9.9.9", "X-Forwarded-For": "1.1.1.1"})) == "9.9.9.9"


@pytest.fixture
async def raw_client():
    """Client without the rate-limit stub of the default fixture."""
    from app.database import pool
    from app.db.schema import prepare_database
    from app.main import app
    from app.utils import redis_client

    await prepare_database()
    if not pool.ready:
        await pool.init()
    redis_client._memory_rate_limit.clear()
    redis = await redis_client.get_redis()
    if redis:
        for pattern in ("login*", "global:*", "refresh*", "2fa*"):
            for key in await redis.keys(pattern):
                await redis.delete(key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    await pool.close()
    redis_client._redis_client = None


async def test_login_limited_per_account_whatever_the_ip(raw_client):
    _, _, _ = await _register(raw_client, "buyer")  # registration itself is limited per IP
    codes = []
    for i in range(12):
        res = await raw_client.post(
            f"{API}/auth/login",
            json={"email": "buyer@example.com", "password": "wrong-password"},
            headers={"X-Forwarded-For": f"8.{i}.8.8"},
        )
        codes.append(res.status_code)
    assert 429 in codes, codes


async def test_refresh_token_is_single_use(raw_client):
    from app.utils import redis_client

    res = await raw_client.post(f"{API}/auth/login", json={"email": "supplier@example.com", "password": "Supplier123!"})
    assert res.status_code == 200, res.text
    old_refresh = res.json()["refresh_token"]
    first = await raw_client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200, first.text
    # After the short grace period for parallel tabs the old token is dead.
    redis = await redis_client.get_redis()
    if redis:
        for key in await redis.keys("refresh_grace:*"):
            await redis.delete(key)
    for key in [k for k in redis_client._memory_rate_limit if k.startswith("grace:")]:
        del redis_client._memory_rate_limit[key]
    again = await raw_client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert again.status_code == 401, again.text
    newest = await raw_client.post(f"{API}/auth/refresh", json={"refresh_token": first.json()["refresh_token"]})
    assert newest.status_code == 200, newest.text


async def test_error_response_does_not_extend_session(raw_client):
    res = await raw_client.post(f"{API}/auth/login", json={"email": "supplier@example.com", "password": "Supplier123!"})
    token = res.json()["access_token"]
    res = await raw_client.post(f"{API}/does-not-exist", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 404
    assert "X-Access-Token" not in res.headers


def test_totp_error_message_is_not_an_oracle():
    from app.utils.totp import totp_failure_user_message

    skew = totp_failure_user_message({"reason": "clock_skew_or_stale_code"})
    wrong = totp_failure_user_message({"reason": "code_not_for_this_secret"})
    assert skew == wrong


def test_production_requires_strong_secret():
    with pytest.raises(Exception):
        Settings(_env_file=None, database_url="postgresql://x", app_env="production")
    Settings(_env_file=None, database_url="postgresql://x", app_env="production", secret_key="s" * 40)


# ------------------------------------------------------------------ C-05 / M-13 files


async def test_html_disguised_as_image_is_rejected(client, buyer_auth):
    res = await client.post(
        f"{API}/buyer/companies/logo",
        headers=buyer_auth["headers"],
        files={"file": ("logo.html", b"<html><script>alert(1)</script></html>", "image/png")},
    )
    assert res.status_code == 422, res.text
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    res = await client.post(
        f"{API}/buyer/companies/logo",
        headers=buyer_auth["headers"],
        files={"file": ("logo.html", png, "image/png")},
    )
    assert res.status_code == 200, res.text
    assert res.json()["logo"].endswith(".png"), "the extension follows the real content"


async def test_uploads_are_never_rendered_as_pages(client):
    from app.main import upload_dir

    target = upload_dir / "audit-tests"
    target.mkdir(parents=True, exist_ok=True)
    (target / "old.html").write_text("<script>alert(1)</script>")
    res = await client.get("/uploads/audit-tests/old.html")
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-disposition"] == "attachment"
    assert "sandbox" in res.headers["content-security-policy"]


def test_csv_export_escapes_formulas():
    from app.modules.admin.services.finance import _csv_safe

    assert _csv_safe('=HYPERLINK("https://evil","x")').startswith("'")
    assert _csv_safe("Поставка") == "Поставка"
    assert _csv_safe(-15.5) == -15.5


# ------------------------------------------------------------- C-02 / M-09 money math


def test_milestone_amounts_add_up_to_contract():
    from app.modules.contracts.services.common import _default_milestones

    for amount in (999.99, 15000.05, 1.07, 12345.67):
        for payment_type in ("split_payment", "milestone", "full_prepayment"):
            parts = [m["amount"] for m in _default_milestones({"agreed_amount": amount}, payment_type)]
            assert round(sum(parts), 2) == amount, (payment_type, amount, parts)


async def test_bank_paid_milestone_is_not_withdrawable_again(client, buyer_auth, supplier_auth):
    from app.modules.payments.services import PaymentService

    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "Audit balance")
    supplier_actor_id = await fetchval("SELECT supplier_actor_id FROM contracts WHERE id = $1", contract_id)
    rows = await fetch("SELECT id, amount, commission_amount FROM payment_milestones WHERE contract_id = $1", contract_id)
    milestone = rows[0]
    await execute("UPDATE payment_milestones SET status = 'released' WHERE id = $1", milestone["id"])
    before = (await PaymentService().supplier_balance(supplier_actor_id)).available

    buyer_actor_id = await fetchval("SELECT buyer_actor_id FROM contracts WHERE id = $1", contract_id)
    await execute(
        """
        INSERT INTO payment_gateway_transactions
            (order_id, gateway, milestone_id, contract_id, buyer_actor_id, amount, currency,
             status, marketplace_meta)
        VALUES ($1, 'alif', $2, $3, $4, $5, 'TJS', 'ok', '{"mpTerminalInfo": []}'::jsonb)
        """,
        f"MS{milestone['id']}-{secrets.token_hex(4)}",
        milestone["id"],
        contract_id,
        buyer_actor_id,
        Decimal(str(milestone["amount"])),
    )
    after = (await PaymentService().supplier_balance(supplier_actor_id)).available
    net = float(milestone["amount"]) - float(milestone["commission_amount"] or 0)
    assert round(before - after, 2) == round(min(net, before), 2)

