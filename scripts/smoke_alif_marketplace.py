#!/usr/bin/env python3
"""Smoke: Alif Marketplace init 100 TJS split 97→610184 + 3→841418.

Creates a payment URL on the sandbox. Card/OTP payment and confirm-delivery
are done manually in the Alif payment page (or via follow-up callbacks).

Usage (from brand_marker_back):
  .venv/bin/python scripts/smoke_alif_marketplace.py
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env without requiring docker
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import get_settings
from app.modules.payments.gateways.alif import AlifClient, build_mp_terminal_info, format_amount


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.alif_enabled or not settings.alif_marketplace_enabled:
        print("FAIL: set PAYMENT_PROVIDER=alif, ALIF_TERMINAL_KEY/PASSWORD and ALIF_TEST_MERCHANT_1/2")
        return 1

    total = Decimal("100.00")
    commission = Decimal("3.00")
    mp_info = build_mp_terminal_info(
        total=total,
        commission=commission,
        supplier_terminal_id=settings.alif_test_merchant_1 or "",
        platform_terminal_id=settings.alif_test_merchant_2 or "",
        title="Smoke Marketplace 100 TJS",
    )
    expected = {
        settings.alif_test_merchant_1: Decimal("97.00"),
        settings.alif_test_merchant_2: Decimal("3.00"),
    }
    for entry in mp_info:
        tid = str(entry["terminal_id"])
        amt = Decimal(format_amount(entry["amount"]))
        if expected.get(tid) != amt:
            print(f"FAIL: split mismatch for {tid}: got {amt}, expected {expected.get(tid)}")
            return 1
    print("OK split:", mp_info)

    order_id = f"SMOKE-{secrets.token_hex(6)}"
    api_base = settings.public_api_url.rstrip("/")
    callback_url = f"{api_base}/api/v1/payments/alif/callback"
    return_url = f"{settings.frontend_url.rstrip('/')}/customer/payments/return?order_id={order_id}"
    print("callback_url:", callback_url)

    client = AlifClient(settings)
    url = await client.create_payment(
        order_id=order_id,
        amount=total,
        callback_url=callback_url,
        return_url=return_url,
        info="Smoke Marketplace 100 TJS 97/3",
        mp_terminal_info=mp_info,
    )
    print("OK payment_url:", url)
    print("order_id:", order_id)
    print(
        "Next: pay with card 5058270283789872 exp 11/26 CVV 340 OTP 12345 "
        "(or Mobi 911002680 OTP 12345), then approve submission / confirm-delivery."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
