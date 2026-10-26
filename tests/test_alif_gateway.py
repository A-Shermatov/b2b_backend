import hashlib
import hmac
import secrets
from decimal import Decimal

import pytest

from app.config import Settings
from app.database import pool
from app.modules.payments.gateways.alif import (
    AlifClient,
    AlifGatewayError,
    build_mp_terminal_info,
    format_amount,
)
from app.modules.payments.services import PaymentService

TERMINAL_KEY = "123456"
TERMINAL_PASSWORD = "test-password"


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://unused",
        "payment_provider": "alif",
        "alif_terminal_key": TERMINAL_KEY,
        "alif_terminal_password": TERMINAL_PASSWORD,
    }
    values.update(overrides)
    return Settings(**values)


def _reference_sign(data: str) -> str:
    secret = hmac.new(TERMINAL_KEY.encode(), TERMINAL_PASSWORD.encode(), hashlib.sha256).hexdigest()
    return hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100, "100.00"),
        (100.5, "100.50"),
        ("1500", "1500.00"),
        (Decimal("10.005"), "10.01"),
        (0.1 + 0.2, "0.30"),
    ],
)
def test_format_amount_always_two_places(value, expected):
    assert format_amount(value) == expected


def test_sign_matches_reference_formula():
    client = AlifClient(_settings())
    data = f"{TERMINAL_KEY}ORDER_1100.50https://example.com/cb"
    token = client.sign(data)
    assert token == _reference_sign(data)
    assert len(token) == 64


def test_client_requires_credentials():
    with pytest.raises(AlifGatewayError):
        AlifClient(_settings(alif_terminal_password=None))


def test_alif_enabled_flag():
    assert _settings().alif_enabled is True
    assert _settings(payment_provider="mock").alif_enabled is False
    assert _settings(alif_terminal_key=None).alif_enabled is False
    assert _settings(alif_terminal_key=None).alif_requested is True
    assert _settings(payment_provider="mock").alif_requested is False


def test_marketplace_enabled_requires_both_merchants():
    assert (
        _settings(alif_test_merchant_1=None, alif_test_merchant_2=None).alif_marketplace_enabled
        is False
    )
    assert (
        _settings(alif_test_merchant_1="610184", alif_test_merchant_2="841418").alif_marketplace_enabled
        is True
    )
    assert (
        _settings(alif_test_merchant_1="610184", alif_test_merchant_2=None).alif_marketplace_enabled
        is False
    )


def test_build_mp_terminal_info_split_97_3():
    info = build_mp_terminal_info(
        total=Decimal("100"),
        commission=Decimal("3"),
        supplier_terminal_id="610184",
        platform_terminal_id="841418",
        title="Smoke",
    )
    assert len(info) == 2
    assert info[0]["terminal_id"] == "610184"
    assert info[0]["amount"] == 97.0
    assert info[1]["terminal_id"] == "841418"
    assert info[1]["amount"] == 3.0


def test_verify_confirm_callback_formula():
    client = AlifClient(_settings())
    payload = {
        "terminal_id": TERMINAL_KEY,
        "transaction_id": "sub-1",
        "status": "ok",
        "event": "confirm",
        "token": _reference_sign(f"{TERMINAL_KEY}sub-1ok"),
    }
    assert client.verify_callback(payload) is True


def test_verify_payment_callback():
    client = AlifClient(_settings())
    payload = {
        "orderId": "MS1-abc",
        "transactionId": "789012",
        "status": "ok",
        "event": "payment",
        "amount": 150.5,
    }
    payload["token"] = _reference_sign("MS1-abcok789012")
    assert client.verify_callback(payload) is True

    assert client.verify_callback({**payload, "status": "failed"}) is False
    assert client.verify_callback({**payload, "orderId": "MS2-abc"}) is False
    assert client.verify_callback({**payload, "token": "0" * 64}) is False
    assert client.verify_callback({k: v for k, v in payload.items() if k != "token"}) is False


def test_verify_cancel_callback_uses_its_own_formula():
    client = AlifClient(_settings())
    payload = {
        "terminal_id": TERMINAL_KEY,
        "transaction_id": "789012",
        "status": "canceled",
        "event": "cancel",
        "token": _reference_sign(f"{TERMINAL_KEY}789012canceled"),
    }
    assert client.verify_callback(payload) is True
    payment_formula_token = _reference_sign("789012canceled")
    assert client.verify_callback({**payload, "token": payment_formula_token}) is False


def test_verify_callback_rejects_unknown_event():
    client = AlifClient(_settings())
    assert client.verify_callback({"event": "refund", "token": "a" * 64}) is False


@pytest.fixture
def alif_settings(monkeypatch):
    settings = _settings()
    monkeypatch.setattr("app.modules.payments.api.callbacks.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.payments.gateways.alif.get_settings", lambda: settings)
    return settings


async def test_callback_endpoint_rejects_forged_signature(client, alif_settings):
    res = await client.post(
        "/api/v1/payments/alif/callback",
        json={
            "orderId": "MS1-forged",
            "transactionId": "1",
            "status": "ok",
            "event": "payment",
            "token": "f" * 64,
        },
    )
    assert res.status_code == 400


async def test_callback_endpoint_acks_unknown_order(client, alif_settings):
    order_id = f"MS0-{secrets.token_hex(6)}"
    res = await client.post(
        "/api/v1/payments/alif/callback",
        json={
            "orderId": order_id,
            "transactionId": "1",
            "status": "ok",
            "event": "payment",
            "token": _reference_sign(f"{order_id}ok1"),
        },
    )
    assert res.status_code == 200
    assert res.json()["ignored"] is True


async def test_payment_config_reports_mock_by_default(client, buyer_auth):
    res = await client.get("/api/v1/buyer/payments/config", headers=buyer_auth["headers"])
    assert res.status_code == 200, res.text
    assert res.json() == {"provider": "mock", "currency": None}


async def test_payment_config_reports_alif_when_enabled(client, buyer_auth, monkeypatch):
    settings = _settings()
    monkeypatch.setattr("app.modules.payments.api.read.get_settings", lambda: settings)
    res = await client.get("/api/v1/buyer/payments/config", headers=buyer_auth["headers"])
    assert res.status_code == 200, res.text
    assert res.json() == {"provider": "alif", "currency": "TJS"}


async def test_payment_config_requires_auth(client):
    res = await client.get("/api/v1/buyer/payments/config")
    assert res.status_code in (401, 403)


async def test_callback_endpoint_disabled_in_mock_mode(client):
    res = await client.post("/api/v1/payments/alif/callback", json={})
    assert res.status_code == 404


@pytest.fixture
async def db_conn():
    from app.db.schema import prepare_database

    await prepare_database()
    if not pool.ready:
        await pool.init()
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            yield conn
        finally:
            await tx.rollback()
    await pool.close()


async def _create_awaiting_milestone(conn, amount: float = 250.5):
    buyer_actor_id = await conn.fetchval("SELECT id FROM actors WHERE side = 'buyer' LIMIT 1")
    supplier_actor_id = await conn.fetchval("SELECT id FROM actors WHERE side = 'supplier' LIMIT 1")
    category_id = await conn.fetchval("SELECT id::text FROM categories LIMIT 1")
    if not (buyer_actor_id and supplier_actor_id and category_id):
        pytest.skip("Seed actors/categories are missing")

    rfq_id = f"alif-test-{secrets.token_hex(6)}"
    await conn.execute(
        """
        INSERT INTO rfqs (
            id, actor_id, created_by, type, title, category_id, budget_type, currency,
            deadline, visibility, status, created_at, updated_at
        )
        VALUES (
            $1, $2, 'test', 'service', 'Alif test', $3, 'fixed', 'TJS',
            '2030-01-01', 'public', 'contract_created', NOW(), NOW()
        )
        """,
        rfq_id,
        buyer_actor_id,
        category_id,
    )
    proposal_id = await conn.fetchval(
        """
        INSERT INTO proposals (rfq_id, supplier_actor_id, price, currency, status, created_at)
        VALUES ($1, $2, $3, 'TJS', 'accepted', NOW())
        RETURNING id
        """,
        rfq_id,
        supplier_actor_id,
        amount,
    )
    contract_id = await conn.fetchval(
        """
        INSERT INTO contracts (
            rfq_id, proposal_id, buyer_actor_id, supplier_actor_id, title, agreed_amount,
            currency, start_date, due_date, payment_type, status, created_at
        )
        VALUES (
            $1, $2, $3, $4, 'Alif test', $5, 'TJS', '2030-01-01', '2030-02-01',
            'full_prepayment', 'pending_payment', NOW()
        )
        RETURNING id
        """,
        rfq_id,
        proposal_id,
        buyer_actor_id,
        supplier_actor_id,
        amount,
    )
    plan_id = await conn.fetchval(
        "INSERT INTO payment_plans (contract_id, payment_type) VALUES ($1, 'full_prepayment') RETURNING id",
        contract_id,
    )
    milestone_id = await conn.fetchval(
        """
        INSERT INTO payment_milestones (
            payment_plan_id, contract_id, title, percentage, amount, trigger, status
        )
        VALUES ($1, $2, 'Предоплата', 100, $3, 'contract_signed', 'awaiting_payment')
        RETURNING id
        """,
        plan_id,
        contract_id,
        amount,
    )
    return {
        "id": milestone_id,
        "contract_id": contract_id,
        "amount": amount,
        "buyer_actor_id": buyer_actor_id,
        "currency": "TJS",
    }


async def test_apply_gateway_status_is_idempotent(db_conn):
    milestone = await _create_awaiting_milestone(db_conn)

    order_id = f"MS{milestone['id']}-{secrets.token_hex(6)}"
    await db_conn.execute(
        """
        INSERT INTO payment_gateway_transactions
            (order_id, gateway, milestone_id, contract_id, buyer_actor_id, amount, currency)
        VALUES ($1, 'alif', $2, $3, $4, $5, $6)
        """,
        order_id,
        milestone["id"],
        milestone["contract_id"],
        milestone["buyer_actor_id"],
        Decimal(format_amount(milestone["amount"])),
        milestone["currency"],
    )
    service = PaymentService(db_conn)
    payload = {"orderId": order_id, "status": "ok", "transactionId": "555", "event": "payment"}

    wrong_amount = await service.apply_gateway_status(
        order_id, "ok", "555", payload, amount=float(milestone["amount"]) + 1
    )
    assert wrong_amount["payment_status"] == "pending"
    assert wrong_amount["milestone_status"] != "funded"

    first = await service.apply_gateway_status(
        order_id, "ok", "555", payload, amount=milestone["amount"]
    )
    assert first["payment_status"] == "ok"
    assert first["milestone_status"] == "funded"

    second = await service.apply_gateway_status(
        order_id, "ok", "555", payload, amount=milestone["amount"]
    )
    assert second == first

    late_failure = await service.apply_gateway_status(order_id, "failed", "555", payload)
    assert late_failure["payment_status"] == "ok"
    assert late_failure["milestone_status"] == "funded"

    contract_status = await db_conn.fetchval(
        "SELECT status::text FROM contracts WHERE id = $1", milestone["contract_id"]
    )
    assert contract_status == "active"


async def test_to_approve_funds_milestone_and_stores_sub_transactions(db_conn):
    milestone = await _create_awaiting_milestone(db_conn, amount=100.0)
    order_id = f"MS{milestone['id']}-{secrets.token_hex(6)}"
    await db_conn.execute(
        """
        INSERT INTO payment_gateway_transactions
            (order_id, gateway, milestone_id, contract_id, buyer_actor_id, amount, currency,
             marketplace_meta)
        VALUES ($1, 'alif', $2, $3, $4, $5, $6, $7::jsonb)
        """,
        order_id,
        milestone["id"],
        milestone["contract_id"],
        milestone["buyer_actor_id"],
        Decimal("100.00"),
        milestone["currency"],
        '{"mpTerminalInfo":[{"terminal_id":"610184","amount":97},{"terminal_id":"841418","amount":3}]}',
    )
    service = PaymentService(db_conn)
    payload = {
        "orderId": order_id,
        "status": "to_approve",
        "transactionId": "parent-9",
        "event": "payment",
        "sub_transactions": [
            {"transaction_id": "sub-a", "terminal_id": "610184"},
            {"transaction_id": "sub-b", "terminal_id": "841418"},
        ],
    }
    result = await service.apply_gateway_status(
        order_id, "to_approve", "parent-9", payload, amount=100.0
    )
    assert result["payment_status"] == "to_approve"
    assert result["milestone_status"] == "funded"

    meta = await db_conn.fetchval(
        "SELECT marketplace_meta::text FROM payment_gateway_transactions WHERE order_id = $1",
        order_id,
    )
    assert "sub-a" in meta
    assert "610184" in meta


async def test_create_payment_sends_marketplace_headers(monkeypatch):
    captured: dict = {}

    async def fake_post(self, path, body, headers=None):
        captured["path"] = path
        captured["body"] = body
        captured["headers"] = headers or {}
        return 200, {"code": 200, "url": "https://test-web.alif.tj/pay/demo"}

    monkeypatch.setattr(AlifClient, "_post", fake_post)
    client = AlifClient(
        _settings(alif_test_merchant_1="610184", alif_test_merchant_2="841418")
    )
    mp = build_mp_terminal_info(
        total=Decimal("100"),
        commission=Decimal("3"),
        supplier_terminal_id="610184",
        platform_terminal_id="841418",
        title="T",
    )
    url = await client.create_payment(
        order_id="MS1-x",
        amount=Decimal("100"),
        callback_url="https://api-b2b.jupiter.tj/api/v1/payments/alif/callback",
        return_url="https://example.com/return",
        mp_terminal_info=mp,
    )
    assert url.endswith("/pay/demo")
    assert captured["headers"].get("isMarketPlace") == "true"
    assert captured["body"]["mpTerminalInfo"] == mp
    assert captured["path"] == "/v2/"


async def test_fund_without_alif_credentials_does_not_fall_back_to_mock(db_conn, monkeypatch):
    settings = _settings(alif_terminal_key=None, alif_terminal_password=None)
    monkeypatch.setattr("app.modules.payments.services.update.get_settings", lambda: settings)
    milestone = await _create_awaiting_milestone(db_conn)
    service = PaymentService(db_conn)

    with pytest.raises(AlifGatewayError):
        await service.fund_milestone(milestone["id"], milestone["buyer_actor_id"])
    with pytest.raises(AlifGatewayError):
        await service.mock_confirm_payment(milestone["id"], milestone["buyer_actor_id"])

    status = await db_conn.fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", milestone["id"]
    )
    assert status == "awaiting_payment"
