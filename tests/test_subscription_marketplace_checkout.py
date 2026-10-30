from decimal import Decimal

import pytest

from app.modules.payments.gateways.alif import build_mp_terminal_info
from tests.sql import fetchrow
from tests.test_alif_gateway import _settings


def test_platform_only_mp_split_sends_full_amount_to_platform_terminal():
    info = build_mp_terminal_info(
        total=Decimal("1990.00"),
        commission=Decimal("1990.00"),
        supplier_terminal_id="merchant-supplier",
        platform_terminal_id="merchant-platform",
        title="Подписка start",
    )
    assert len(info) == 1
    assert info[0]["terminal_id"] == "merchant-platform"
    assert info[0]["amount"] == 1990.0


@pytest.mark.asyncio
async def test_subscription_checkout_uses_marketplace_when_enabled(client, supplier_auth, monkeypatch):
    settings = _settings(
        alif_test_merchant_1="m1",
        alif_test_merchant_2="m2",
    )
    assert settings.alif_marketplace_enabled

    captured = {}

    async def fake_create_payment(self, **kwargs):
        captured.update(kwargs)
        return "https://alif.test/pay/sub"

    monkeypatch.setattr(
        "app.modules.subscription.services.payment.get_settings", lambda: settings
    )
    monkeypatch.setattr(
        "app.modules.payments.gateways.alif.AlifClient.create_payment",
        fake_create_payment,
    )

    response = await client.post(
        "/api/v1/supplier/subscription/checkout",
        json={"plan": "start", "method": "alif"},
        headers=supplier_auth["headers"],
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["payment_url"] == "https://alif.test/pay/sub"
    assert data["order_id"] and data["order_id"].startswith("SUB")
    assert captured.get("mp_terminal_info")
    assert len(captured["mp_terminal_info"]) == 1
    assert captured["mp_terminal_info"][0]["terminal_id"] == "m2"
    assert captured["mp_terminal_info"][0]["amount"] == float(captured["amount"])

    row = await fetchrow(
        "SELECT marketplace_meta FROM subscription_payment_orders WHERE order_id = $1",
        data["order_id"],
    )
    assert row["marketplace_meta"] is not None
    meta = row["marketplace_meta"]
    if isinstance(meta, str):
        import json

        meta = json.loads(meta)
    assert meta["mpTerminalInfo"][0]["terminal_id"] == "m2"
