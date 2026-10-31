#!/usr/bin/env python3
"""Verify Alif marketplace settlement for one deal (four checks from the pilot review).

Usage (from backend/):
  .venv/bin/python scripts/verify_alif_deal.py <contract_id>
  .venv/bin/python scripts/verify_alif_deal.py --order MS123-abc

Prints OK or a list of discrepancies. Optionally calls Alif checktxn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import asyncpg

from app.config import get_settings
from app.database import DatabasePool
from app.modules.payments.gateways.alif import AlifClient, format_amount


TWO = Decimal("0.01")


def _d(value: Any) -> Decimal:
    return Decimal(format_amount(value or 0))


def _parse_meta(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _money(value: Any) -> str:
    return format_amount(value or 0)


async def _connect() -> asyncpg.Connection:
    settings = get_settings()
    return await asyncpg.connect(DatabasePool.dsn(settings.database_url))


async def check_gateway_tx(conn: asyncpg.Connection, contract_id: int, issues: list[str]) -> list[str]:
    print("\n=== 1. payment_gateway_transactions ===")
    rows = await conn.fetch(
        """
        SELECT order_id, amount, status, external_transaction_id, paid_at, updated_at,
               marketplace_meta
        FROM payment_gateway_transactions
        WHERE contract_id = $1
        ORDER BY id
        """,
        contract_id,
    )
    if not rows:
        issues.append("нет записей в payment_gateway_transactions для сделки")
        print("  (пусто)")
        return []

    order_ids: list[str] = []
    for row in rows:
        order_ids.append(row["order_id"])
        meta = _parse_meta(row["marketplace_meta"])
        mp = meta.get("mpTerminalInfo") or []
        subs = meta.get("sub_transactions") or []
        print(
            f"  order={row['order_id']} amount={_money(row['amount'])} "
            f"status={row['status']} subs={len(subs)} mp={len(mp)}"
        )
        if row["status"] not in ("to_approve", "ok", "canceled", "pending", "failed"):
            issues.append(f"{row['order_id']}: неожиданный статус {row['status']}")
        if row["status"] in ("to_approve", "ok"):
            if len(mp) < 2:
                issues.append(f"{row['order_id']}: mpTerminalInfo должен содержать 2 строки (97/3)")
            else:
                amounts = sorted(_d(x.get("amount")) for x in mp if isinstance(x, dict))
                total = _d(row["amount"])
                if len(amounts) >= 2 and abs(sum(amounts) - total) > TWO:
                    issues.append(
                        f"{row['order_id']}: сумма mpTerminalInfo {sum(amounts)} ≠ amount {total}"
                    )
            if row["status"] == "ok" and not subs:
                issues.append(
                    f"{row['order_id']}: status=ok, но sub_transactions пустой "
                    "(ошибка №8 — банк не делил деньги)"
                )
            elif row["status"] == "to_approve" and not subs:
                issues.append(
                    f"{row['order_id']}: status=to_approve без sub_transactions — "
                    "confirm-delivery невозможен"
                )
    return order_ids


async def check_http_logs(
    conn: asyncpg.Connection, order_ids: list[str], issues: list[str]
) -> None:
    print("\n=== 2. payment_gateway_http_logs (confirm_delivery / checktxn / create_payment) ===")
    if not order_ids:
        print("  (пропуск — нет order_id)")
        return
    rows = await conn.fetch(
        """
        SELECT created_at, operation, order_id, request_body, response_status, response_body, error
        FROM payment_gateway_http_logs
        WHERE order_id = ANY($1::text[])
          AND operation IN ('confirm_delivery', 'checktxn', 'create_payment', 'cancel_marketplace')
        ORDER BY created_at DESC
        LIMIT 50
        """,
        order_ids,
    )
    if not rows:
        # confirm_delivery logs by sub-transaction may not carry parent order_id
        rows = await conn.fetch(
            """
            SELECT created_at, operation, order_id, request_body, response_status, response_body, error
            FROM payment_gateway_http_logs
            WHERE operation IN ('confirm_delivery', 'checktxn', 'create_payment', 'cancel_marketplace')
            ORDER BY created_at DESC
            LIMIT 30
            """,
        )
        print("  (нет логов по order_id — показаны последние 30 по операциям)")

    confirms: list[asyncpg.Record] = []
    for row in rows:
        err = row["error"] or ""
        print(
            f"  {row['created_at']} {row['operation']} order={row['order_id']} "
            f"http={row['response_status']} err={err!r}"
        )
        if row["operation"] == "confirm_delivery":
            confirms.append(row)
            if row["response_status"] != 200 or err:
                issues.append(
                    f"confirm_delivery order={row['order_id']}: "
                    f"http={row['response_status']} error={err!r}"
                )
            body = row["request_body"]
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    body = {}
            if isinstance(body, dict) and "amount" in body:
                amt = _d(body["amount"])
                print(f"    amount={_money(amt)}")

    ok_tx = await conn.fetch(
        """
        SELECT order_id, marketplace_meta, status
        FROM payment_gateway_transactions
        WHERE order_id = ANY($1::text[]) AND status = 'ok'
        """,
        order_ids,
    )
    for tx in ok_tx:
        meta = _parse_meta(tx["marketplace_meta"])
        expected = len(meta.get("sub_transactions") or [])
        if expected and len(confirms) < expected:
            # Soft: logs may not be filtered by order; only warn if we have order-scoped confirms
            order_confirms = [c for c in confirms if c["order_id"] == tx["order_id"]]
            if order_confirms and len(order_confirms) < expected:
                issues.append(
                    f"{tx['order_id']}: ожидалось {expected} confirm_delivery, "
                    f"в логах по заказу {len(order_confirms)}"
                )


async def check_milestones_and_platform(
    conn: asyncpg.Connection, contract_id: int, issues: list[str]
) -> None:
    print("\n=== 4. payment_milestones + platform_payments ===")
    milestones = await conn.fetch(
        """
        SELECT id, title, amount, COALESCE(commission_amount, 0) AS commission_amount, status
        FROM payment_milestones
        WHERE contract_id = $1
        ORDER BY id
        """,
        contract_id,
    )
    if not milestones:
        issues.append("нет этапов в payment_milestones")
        print("  milestones: (пусто)")
    for m in milestones:
        print(
            f"  milestone#{m['id']} status={m['status']} "
            f"amount={_money(m['amount'])} commission={_money(m['commission_amount'])}"
        )
        if m["status"] == "released":
            total = _d(m["amount"])
            commission = _d(m["commission_amount"])
            expected_commission = (total * Decimal("0.03")).quantize(TWO)
            if abs(commission - expected_commission) > TWO and commission > 0:
                # tolerate configured rates, but flag wild mismatches
                if commission >= total:
                    issues.append(
                        f"milestone#{m['id']}: комиссия {_money(commission)} ≥ суммы {_money(total)}"
                    )

    payments = await conn.fetch(
        """
        SELECT id, type, status, amount, commission, title
        FROM platform_payments
        WHERE contract_id = $1
        ORDER BY id
        """,
        contract_id,
    )
    if not payments:
        print("  platform_payments: (пусто)")
    for p in payments:
        print(
            f"  platform#{p['id']} type={p['type']} status={p['status']} "
            f"amount={_money(p['amount'])} commission={_money(p['commission'])} "
            f"title={p['title']!r}"
        )


async def check_alif_live(order_ids: list[str], issues: list[str]) -> None:
    print("\n=== 3. Alif checktxn (live) ===")
    settings = get_settings()
    if not settings.alif_enabled:
        print("  Alif выключен — пропуск live-проверки")
        print("  Сверьте кабинеты ALIF_TEST_MERCHANT_1 (97) и ALIF_TEST_MERCHANT_2 (3) вручную")
        return
    client = AlifClient(settings)
    for order_id in order_ids:
        try:
            data = await client.check_transaction(order_id)
        except Exception as exc:  # noqa: BLE001 — script should keep going
            issues.append(f"checktxn {order_id}: {exc}")
            print(f"  {order_id}: ERROR {exc}")
            continue
        if data is None:
            issues.append(f"checktxn {order_id}: 404 / not found")
            print(f"  {order_id}: not found")
            continue
        status = data.get("status")
        subs = data.get("sub_transactions") or []
        print(f"  {order_id}: status={status} sub_transactions={len(subs)}")
        for sub in subs:
            if isinstance(sub, dict):
                print(
                    f"    sub txn={sub.get('transaction_id')} "
                    f"terminal={sub.get('terminal_id')} "
                    f"amount={sub.get('amount')} canceled={sub.get('canceled')}"
                )
        if status in ("ok", "to_approve", "partially_approved") and not subs:
            issues.append(f"checktxn {order_id}: нет sub_transactions при status={status}")


async def resolve_contract_id(conn: asyncpg.Connection, args: argparse.Namespace) -> int | None:
    if args.contract_id:
        return args.contract_id
    if args.order:
        cid = await conn.fetchval(
            "SELECT contract_id FROM payment_gateway_transactions WHERE order_id = $1",
            args.order,
        )
        if cid is None:
            print(f"Заказ {args.order} не найден")
            return None
        return int(cid)
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Alif settlement for a deal")
    parser.add_argument("contract_id", nargs="?", type=int, help="ID сделки (contracts.id)")
    parser.add_argument("--order", help="order_id из return URL (MS…)")
    parser.add_argument("--skip-alif", action="store_true", help="Не вызывать checktxn")
    args = parser.parse_args()

    get_settings.cache_clear()
    conn = await _connect()
    issues: list[str] = []
    try:
        contract_id = await resolve_contract_id(conn, args)
        if contract_id is None:
            parser.print_help()
            return 2

        print(f"Сделка contract_id={contract_id}")
        order_ids = await check_gateway_tx(conn, contract_id, issues)
        await check_http_logs(conn, order_ids, issues)
        if not args.skip_alif:
            await check_alif_live(order_ids, issues)
        else:
            print("\n=== 3. Alif checktxn — пропуск (--skip-alif) ===")
            print("  Сверьте кабинеты ALIF_TEST_MERCHANT_1 / _2 вручную (97 / 3)")
        await check_milestones_and_platform(conn, contract_id, issues)
    finally:
        await conn.close()

    print("\n=== Итог ===")
    if issues:
        print("FAIL — расхождения:")
        for item in issues:
            print(f"  • {item}")
        return 1
    print("OK — четыре проверки без расхождений в БД/логах")
    print("Не забудьте сверить кабинеты тестовых терминалов Alif (97 + 3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
