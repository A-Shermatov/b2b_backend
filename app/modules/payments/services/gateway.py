import json
import logging
import secrets
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from app.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.payments.gateways.alif import (
    AlifClient,
    AlifGatewayError,
    build_mp_terminal_info,
    format_amount,
)
from app.modules.payments.services.update import (
    FUNDABLE_MILESTONE_STATUSES,
    ensure_contract_fundable,
)
from app.utils.audit import log_event

logger = logging.getLogger(__name__)

GATEWAY_ALIF = "alif"
FINAL_STATUSES = ("ok", "failed", "canceled")
# Marketplace hold after successful card payment — treat as escrow funded.
HOLD_STATUSES = ("to_approve", "ok", "partially_approved")
PENDING_REUSE_MINUTES = 30


class InvalidCallbackSignature(Exception):
    pass


def _parse_meta(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class PaymentGatewayMixin:
    async def start_alif_payment(self, milestone_id: int, buyer_id: int) -> dict:
        settings = get_settings()
        async with self.connection() as conn:
            async with conn.transaction():
                row = await self._load_milestone_contract(conn, milestone_id, for_update=True)
                if row["buyer_actor_id"] != buyer_id:
                    raise ForbiddenError("Access denied")
                if row["status"] == "funded":
                    return {"id": row["id"], "status": row["status"]}
                ensure_contract_fundable(row)
                if row["status"] not in FUNDABLE_MILESTONE_STATUSES:
                    raise ConflictError("Milestone cannot be funded")
                if row["currency"] != settings.alif_currency:
                    raise ConflictError(
                        f"Оплата через Alif доступна только в {settings.alif_currency}"
                    )

                existing = await conn.fetchrow(
                    """
                    SELECT order_id, payment_url
                    FROM payment_gateway_transactions
                    WHERE milestone_id = $1
                      AND gateway = $2
                      AND status = 'pending'
                      AND payment_url IS NOT NULL
                      AND created_at > NOW() - make_interval(mins => $3)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    milestone_id,
                    GATEWAY_ALIF,
                    PENDING_REUSE_MINUTES,
                )
                if existing:
                    return {
                        "id": row["id"],
                        "status": row["status"],
                        "payment_status": "pending",
                        "order_id": existing["order_id"],
                        "payment_url": existing["payment_url"],
                    }

                amount = Decimal(format_amount(row["amount"]))
                commission = Decimal(format_amount(row["commission_amount"] or 0))
                order_id = f"MS{milestone_id}-{secrets.token_hex(6)}"
                marketplace_meta: dict[str, Any] | None = None
                mp_info = None
                if settings.alif_marketplace_enabled:
                    mp_info = build_mp_terminal_info(
                        total=amount,
                        commission=commission,
                        supplier_terminal_id=settings.alif_test_merchant_1 or "",
                        platform_terminal_id=settings.alif_test_merchant_2 or "",
                        title=row["title"] or f"Сделка #{row['contract_id']}",
                    )
                    marketplace_meta = {"mpTerminalInfo": mp_info}

                await conn.execute(
                    """
                    INSERT INTO payment_gateway_transactions
                        (order_id, gateway, milestone_id, contract_id, buyer_actor_id,
                         amount, currency, marketplace_meta)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    order_id,
                    GATEWAY_ALIF,
                    milestone_id,
                    row["contract_id"],
                    buyer_id,
                    amount,
                    row["currency"],
                    json.dumps(marketplace_meta) if marketplace_meta else None,
                )

        api_base = settings.public_api_url.rstrip("/")
        callback_url = f"{api_base}{settings.api_v1_prefix}/payments/alif/callback"
        query = urlencode({"order_id": order_id, "contract_id": row["contract_id"]})
        return_url = f"{settings.frontend_url.rstrip('/')}/customer/payments/return?{query}"

        try:
            payment_url = await AlifClient(settings).create_payment(
                order_id=order_id,
                amount=amount,
                callback_url=callback_url,
                return_url=return_url,
                info=f"Оплата этапа «{row['title']}» по сделке #{row['contract_id']}",
                mp_terminal_info=mp_info,
            )
        except AlifGatewayError:
            async with self.connection() as conn:
                await conn.execute(
                    """
                    UPDATE payment_gateway_transactions
                    SET status = 'failed', updated_at = NOW()
                    WHERE order_id = $1
                    """,
                    order_id,
                )
            raise

        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE payment_gateway_transactions
                SET payment_url = $2, updated_at = NOW()
                WHERE order_id = $1
                """,
                order_id,
                payment_url,
            )
        logger.info(
            "Alif payment created: order=%s milestone=%s marketplace=%s",
            order_id,
            milestone_id,
            bool(mp_info),
        )
        return {
            "id": row["id"],
            "status": row["status"],
            "payment_status": "pending",
            "order_id": order_id,
            "payment_url": payment_url,
        }

    async def apply_gateway_status(
        self,
        order_id: str,
        status: str,
        transaction_id: Any,
        payload: dict[str, Any],
        amount: Any = None,
    ) -> dict:
        external_id = str(transaction_id) if transaction_id not in (None, "") else None
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        normalized = status.strip().lower()

        async with self.connection() as conn:
            async with conn.transaction():
                tx = await conn.fetchrow(
                    """
                    SELECT id, order_id, milestone_id, contract_id, amount, status,
                           marketplace_meta
                    FROM payment_gateway_transactions
                    WHERE order_id = $1
                    FOR UPDATE
                    """,
                    order_id,
                )
                if not tx:
                    raise NotFoundError("Payment order not found")

                if tx["status"] == normalized or tx["status"] == "canceled":
                    return await self._gateway_result(conn, tx, tx["status"])
                # Once held/paid, ignore late failures and duplicate hold/ok callbacks.
                if tx["status"] in ("ok", "to_approve") and normalized not in (
                    "canceled",
                    "partially_canceled",
                ):
                    return await self._gateway_result(conn, tx, tx["status"])
                if tx["status"] == "failed" and normalized not in HOLD_STATUSES:
                    return await self._gateway_result(conn, tx, tx["status"])

                meta = _parse_meta(tx["marketplace_meta"])
                subs = payload.get("sub_transactions")
                if isinstance(subs, list):
                    meta["sub_transactions"] = subs
                    await conn.execute(
                        """
                        UPDATE payment_gateway_transactions
                        SET marketplace_meta = $2::jsonb, updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        json.dumps(meta, ensure_ascii=False, default=str),
                    )

                if normalized in HOLD_STATUSES:
                    if amount is not None and format_amount(amount) != format_amount(tx["amount"]):
                        logger.error(
                            "Alif amount mismatch: order=%s expected=%s got=%s",
                            order_id,
                            tx["amount"],
                            amount,
                        )
                        await conn.execute(
                            """
                            UPDATE payment_gateway_transactions
                            SET raw_callback = $2::jsonb, updated_at = NOW()
                            WHERE id = $1
                            """,
                            tx["id"],
                            raw,
                        )
                        return await self._gateway_result(conn, tx, tx["status"])

                    store_status = "to_approve" if normalized == "to_approve" else "ok"
                    if normalized == "partially_approved":
                        store_status = "to_approve"
                    await conn.execute(
                        """
                        UPDATE payment_gateway_transactions
                        SET status = $2,
                            external_transaction_id = COALESCE($3, external_transaction_id),
                            transaction_type = COALESCE($4, transaction_type),
                            account_masked = COALESCE($5, account_masked),
                            raw_callback = $6::jsonb,
                            paid_at = COALESCE(paid_at, NOW()),
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        store_status,
                        external_id,
                        payload.get("transaction_type") or None,
                        payload.get("account") or None,
                        raw,
                    )
                    milestone = await self._load_milestone_contract(
                        conn, tx["milestone_id"], for_update=True
                    )
                    await log_event(
                        conn,
                        "payment.callback.success",
                        "payment",
                        order_id,
                        actor_id=milestone["buyer_actor_id"],
                        details={
                            "order_id": order_id,
                            "milestone_id": tx["milestone_id"],
                            "contract_id": tx["contract_id"],
                            "amount": tx["amount"],
                            "gateway_status": normalized,
                            "before": {"status": tx["status"]},
                            "after": {"status": store_status},
                        },
                    )
                    if milestone["status"] in FUNDABLE_MILESTONE_STATUSES:
                        await self._apply_milestone_funded(conn, milestone, source="gateway")
                    else:
                        logger.warning(
                            "Alif hold/ok but milestone %s is %s (order=%s)",
                            tx["milestone_id"],
                            milestone["status"],
                            order_id,
                        )
                    return await self._gateway_result(conn, tx, store_status)

                if normalized in ("failed", "canceled", "partially_canceled"):
                    store = "canceled" if "cancel" in normalized else normalized
                    await conn.execute(
                        """
                        UPDATE payment_gateway_transactions
                        SET status = $2,
                            external_transaction_id = COALESCE($3, external_transaction_id),
                            raw_callback = $4::jsonb,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        store,
                        external_id,
                        raw,
                    )
                    await log_event(
                        conn,
                        "payment.callback.fail",
                        "payment",
                        order_id,
                        details={
                            "order_id": order_id,
                            "milestone_id": tx["milestone_id"],
                            "contract_id": tx["contract_id"],
                            "amount": tx["amount"],
                            "gateway_status": normalized,
                            "before": {"status": tx["status"]},
                            "after": {"status": store},
                        },
                    )
                    if store == "canceled":
                        logger.warning(
                            "Alif payment canceled: order=%s milestone=%s",
                            order_id,
                            tx["milestone_id"],
                        )
                    return await self._gateway_result(conn, tx, store)

                await conn.execute(
                    """
                    UPDATE payment_gateway_transactions
                    SET external_transaction_id = COALESCE($2, external_transaction_id),
                        raw_callback = $3::jsonb,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    tx["id"],
                    external_id,
                    raw,
                )
                return await self._gateway_result(conn, tx, tx["status"])

    async def _gateway_result(self, conn, tx, payment_status: str) -> dict:
        milestone_status = await conn.fetchval(
            "SELECT status::text FROM payment_milestones WHERE id = $1", tx["milestone_id"]
        )
        return {
            "order_id": tx["order_id"],
            "payment_status": payment_status,
            "milestone_id": tx["milestone_id"],
            "contract_id": tx["contract_id"],
            "milestone_status": milestone_status,
        }

    async def sync_alif_order(self, order_id: str, buyer_id: int) -> dict:
        async with self.connection() as conn:
            tx = await conn.fetchrow(
                """
                SELECT id, order_id, milestone_id, contract_id, amount, status, buyer_actor_id
                FROM payment_gateway_transactions
                WHERE order_id = $1 AND gateway = $2
                """,
                order_id,
                GATEWAY_ALIF,
            )
            if not tx or tx["buyer_actor_id"] != buyer_id:
                raise NotFoundError("Payment order not found")
            if tx["status"] in FINAL_STATUSES or not get_settings().alif_enabled:
                return await self._gateway_result(conn, tx, tx["status"])

        data = await AlifClient().check_transaction(order_id)
        if data is None:
            async with self.connection() as conn:
                return await self._gateway_result(conn, tx, tx["status"])
        return await self.apply_gateway_status(
            order_id,
            str(data.get("status", "")),
            data.get("transactionId"),
            data,
            amount=data.get("amount"),
        )

    async def _find_order_by_sub_transaction(self, conn, transaction_id: str) -> str | None:
        order_id = await conn.fetchval(
            """
            SELECT order_id FROM payment_gateway_transactions
            WHERE external_transaction_id = $1 AND gateway = $2
            """,
            transaction_id,
            GATEWAY_ALIF,
        )
        if order_id:
            return order_id
        return await conn.fetchval(
            """
            SELECT order_id FROM payment_gateway_transactions
            WHERE gateway = $2
              AND marketplace_meta IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    COALESCE(marketplace_meta->'sub_transactions', '[]'::jsonb)
                ) AS st
                WHERE st->>'transaction_id' = $1
              )
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            transaction_id,
            GATEWAY_ALIF,
        )

    async def handle_alif_callback(self, payload: dict[str, Any]) -> dict:
        if not AlifClient().verify_callback(payload):
            raise InvalidCallbackSignature()

        event = payload.get("event") or "payment"
        if event == "payment":
            order_id = str(payload.get("orderId", ""))
            if order_id.startswith("SUB"):
                from app.modules.subscription.services import SubscriptionService

                try:
                    return await SubscriptionService().apply_subscription_gateway_status(
                        order_id,
                        str(payload.get("status", "")),
                        payload.get("transactionId"),
                        payload,
                        amount=payload.get("amount"),
                    )
                except NotFoundError:
                    logger.warning("Alif subscription callback for unknown order=%s", order_id)
                    return {"order_id": order_id, "ignored": True}
            try:
                return await self.apply_gateway_status(
                    order_id,
                    str(payload.get("status", "")),
                    payload.get("transactionId"),
                    payload,
                    amount=payload.get("amount"),
                )
            except NotFoundError:
                logger.warning("Alif callback for unknown order=%s", order_id)
                return {"order_id": order_id, "ignored": True}

        if event in ("cancel", "confirm"):
            transaction_id = str(payload.get("transaction_id", ""))
            async with self.connection() as conn:
                order_id = await self._find_order_by_sub_transaction(conn, transaction_id)
            if not order_id:
                logger.warning(
                    "Alif %s callback for unknown transaction=%s", event, transaction_id
                )
                return {"transaction_id": transaction_id, "ignored": True}
            status = str(payload.get("parent_status") or payload.get("status") or "")
            return await self.apply_gateway_status(
                order_id, status, payload.get("parent_transaction_id") or transaction_id, payload
            )

        logger.info("Alif callback with unsupported event=%s ignored", event)
        return {"ignored": True}

    async def confirm_marketplace_delivery_for_milestone(self, milestone_id: int) -> None:
        """Call Alif POST /confirm-delivery for held sub-transactions of one milestone."""
        settings = get_settings()
        if not settings.alif_marketplace_enabled:
            return

        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, order_id, contract_id, amount, marketplace_meta
                FROM payment_gateway_transactions
                WHERE milestone_id = $1
                  AND gateway = $2
                  AND status = 'to_approve'
                ORDER BY id
                """,
                milestone_id,
                GATEWAY_ALIF,
            )
        await self._confirm_marketplace_rows(AlifClient(settings), rows)

    async def confirm_marketplace_deliveries_for_contract(self, contract_id: int) -> None:
        """Call Alif POST /confirm-delivery for held marketplace sub-transactions."""
        settings = get_settings()
        if not settings.alif_marketplace_enabled:
            return

        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, order_id, contract_id, amount, marketplace_meta
                FROM payment_gateway_transactions
                WHERE contract_id = $1
                  AND gateway = $2
                  AND status = 'to_approve'
                ORDER BY id
                """,
                contract_id,
                GATEWAY_ALIF,
            )
        await self._confirm_marketplace_rows(AlifClient(settings), rows)

    async def _refresh_sub_transactions(self, client: AlifClient, row) -> list:
        """Fetch sub_transactions via checktxn and persist them when callback missed them."""
        try:
            data = await client.check_transaction(row["order_id"])
        except AlifGatewayError as exc:
            logger.warning(
                "Alif checktxn failed while refreshing sub_transactions for order=%s: %s",
                row["order_id"],
                exc.detail if hasattr(exc, "detail") else exc,
            )
            return []
        if not isinstance(data, dict):
            return []
        subs = data.get("sub_transactions")
        if not isinstance(subs, list) or not subs:
            return []

        meta = _parse_meta(row["marketplace_meta"])
        meta["sub_transactions"] = subs
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE payment_gateway_transactions
                SET marketplace_meta = $2::jsonb, updated_at = NOW()
                WHERE id = $1
                """,
                row["id"],
                json.dumps(meta, ensure_ascii=False, default=str),
            )
        return subs

    async def _confirm_marketplace_rows(self, client: AlifClient, rows) -> None:
        for row in rows:
            meta = _parse_meta(row["marketplace_meta"])
            mp_info = meta.get("mpTerminalInfo") or []
            subs = meta.get("sub_transactions") or []
            if not subs:
                logger.warning(
                    "No sub_transactions in meta for order=%s contract=%s — trying checktxn",
                    row["order_id"],
                    row["contract_id"],
                )
                subs = await self._refresh_sub_transactions(client, row)
                if subs:
                    meta["sub_transactions"] = subs
            if not subs:
                raise ConflictError(
                    f"Alif: нет подтранзакций для заказа {row['order_id']}. "
                    "Банк не разделил платёж — подтверждение доставки невозможно. "
                    "Повторите после появления sub_transactions в checktxn."
                )

            amount_by_terminal = {
                str(item.get("terminal_id")): Decimal(format_amount(item.get("amount") or 0))
                for item in mp_info
                if isinstance(item, dict)
            }
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                sub_id = str(sub.get("transaction_id") or "")
                if not sub_id:
                    continue
                terminal_id = str(sub.get("terminal_id") or "")
                amount = amount_by_terminal.get(terminal_id)
                if amount is None or amount <= 0:
                    # Fallback: equal split of total if meta incomplete
                    amount = Decimal(format_amount(row["amount"])) / max(len(subs), 1)
                await client.confirm_delivery(transaction_id=sub_id, amount=amount)

            async with self.connection() as conn:
                await conn.execute(
                    """
                    UPDATE payment_gateway_transactions
                    SET status = 'ok', updated_at = NOW()
                    WHERE id = $1 AND status = 'to_approve'
                    """,
                    row["id"],
                )

    async def cancel_marketplace_for_contract(
        self, contract_id: int, reason: str = "Отмена сделки"
    ) -> None:
        """Cancel Alif marketplace transactions of a contract.

        Every transaction is attempted. Transactions whose sub-transactions were all
        canceled are marked canceled locally; failed ones stay untouched and an
        AlifGatewayError is raised after all transactions were processed.
        """
        settings = get_settings()
        if not settings.alif_marketplace_enabled:
            return

        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, order_id, amount, marketplace_meta
                FROM payment_gateway_transactions
                WHERE contract_id = $1
                  AND gateway = $2
                  AND status IN ('to_approve', 'pending', 'ok')
                ORDER BY id
                """,
                contract_id,
                GATEWAY_ALIF,
            )

        if not rows:
            return

        client = AlifClient(settings)
        failed_orders: list[str] = []
        for row in rows:
            meta = _parse_meta(row["marketplace_meta"])
            mp_info = meta.get("mpTerminalInfo") or []
            subs = meta.get("sub_transactions") or []
            amount_by_terminal = {
                str(item.get("terminal_id")): Decimal(format_amount(item.get("amount") or 0))
                for item in mp_info
                if isinstance(item, dict)
            }
            row_failed = False
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                sub_id = str(sub.get("transaction_id") or "")
                if not sub_id or sub.get("canceled"):
                    continue
                terminal_id = str(sub.get("terminal_id") or "")
                amount = amount_by_terminal.get(terminal_id) or Decimal(
                    format_amount(row["amount"])
                )
                try:
                    await client.cancel_marketplace(
                        transaction_id=sub_id, amount=amount, reason=reason
                    )
                    sub["canceled"] = True
                except AlifGatewayError as exc:
                    row_failed = True
                    logger.warning(
                        "Alif cancel failed for contract=%s sub=%s: %s",
                        contract_id,
                        sub_id,
                        exc.detail,
                    )

            if row_failed:
                failed_orders.append(row["order_id"])
                async with self.connection() as conn:
                    await conn.execute(
                        """
                        UPDATE payment_gateway_transactions
                        SET marketplace_meta = $2::jsonb, updated_at = NOW()
                        WHERE id = $1
                        """,
                        row["id"],
                        json.dumps(meta, ensure_ascii=False, default=str),
                    )
                continue

            async with self.connection() as conn:
                await conn.execute(
                    """
                    UPDATE payment_gateway_transactions
                    SET status = 'canceled', updated_at = NOW()
                    WHERE id = $1 AND status IN ('to_approve', 'pending', 'ok')
                    """,
                    row["id"],
                )

        if failed_orders:
            raise AlifGatewayError(
                f"Alif: не удалось отменить платежи сделки #{contract_id} "
                f"({', '.join(failed_orders)})"
            )
