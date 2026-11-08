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
    marketplace_commission,
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
# A pending order whose payment_url is not saved yet is still being created.
PENDING_CREATE_MINUTES = 2
# Milestone statuses whose bank hold may be released by the buyer's acceptance.
BUYER_RELEASABLE_STATUSES = ("funded",)
NOT_DUPLICATE_SQL = "COALESCE(t.marketplace_meta->>'duplicate', 'false') <> 'true'"
# Gateway order plus the commission of its milestone (to rebuild a missing split).
_TX_COLUMNS = """
    t.id, t.order_id, t.contract_id, t.amount, t.status, t.marketplace_meta,
    COALESCE(m.commission_amount, 0) AS commission_amount
    FROM payment_gateway_transactions t
    LEFT JOIN payment_milestones m ON m.id = t.milestone_id
"""


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


def _amounts_by_terminal(mp_info) -> dict[str, Decimal]:
    return {
        str(item.get("terminal_id")): Decimal(format_amount(item.get("amount") or 0))
        for item in mp_info
        if isinstance(item, dict)
    }


def _terminal_amounts(row, meta: dict) -> dict[str, Decimal]:
    """Amount per terminal as sent to the bank; rebuilt for orders saved without it."""
    mp_info = meta.get("mpTerminalInfo") or []
    if not mp_info:
        settings = get_settings()
        mp_info = build_mp_terminal_info(
            total=Decimal(format_amount(row["amount"])),
            commission=Decimal(format_amount(row["commission_amount"] or 0)),
            supplier_terminal_id=settings.alif_test_merchant_1 or "",
            platform_terminal_id=settings.alif_test_merchant_2 or "",
            title="",
        )
    return _amounts_by_terminal(mp_info)


def _sub_amount(row, sub: dict, amount_by_terminal: dict[str, Decimal], sub_count: int) -> Decimal:
    """Amount of one sub-transaction; never guessed when several parts exist."""
    terminal_id = str(sub.get("terminal_id") or "")
    amount = amount_by_terminal.get(terminal_id)
    if amount is not None and amount > 0:
        return amount
    if sub_count == 1:
        return Decimal(format_amount(row["amount"]))
    raise ConflictError(
        f"Alif: неизвестная сумма подтранзакции {sub.get('transaction_id')} "
        f"(терминал {terminal_id or '—'}) в заказе {row['order_id']}"
    )


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

                # A pending order without payment_url is being created right now by a
                # concurrent request (double click): never start a second one.
                existing = await conn.fetchrow(
                    """
                    SELECT order_id, payment_url
                    FROM payment_gateway_transactions
                    WHERE milestone_id = $1
                      AND gateway = $2
                      AND status = 'pending'
                      AND (
                        (payment_url IS NOT NULL
                         AND created_at > NOW() - make_interval(mins => $3))
                        OR (payment_url IS NULL
                            AND created_at > NOW() - make_interval(mins => $4))
                      )
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    milestone_id,
                    GATEWAY_ALIF,
                    PENDING_REUSE_MINUTES,
                    PENDING_CREATE_MINUTES,
                )
                if existing and not existing["payment_url"]:
                    raise ConflictError(
                        "Платёж уже создаётся. Обновите страницу через несколько секунд."
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
                    split_commission = marketplace_commission(amount, commission)
                    if split_commission != commission:
                        # Keep the ledger in line with what the bank will actually split.
                        await self._set_milestone_commission(conn, row, split_commission)
                        commission = split_commission
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
        result = await self._apply_gateway_status(
            order_id, status, transaction_id, payload, amount
        )
        reason = result.pop("refund_reason", None)
        if result.pop("refund_required", False):
            await self._refund_late_payment(order_id, reason or "Сделка отменена до оплаты")
        elif status.strip().lower() in HOLD_STATUSES:
            # A deal paid "at once" goes to the supplier as soon as the bank holds it.
            async with self.connection() as conn:
                milestone_id = await conn.fetchval(
                    "SELECT milestone_id FROM payment_gateway_transactions WHERE order_id = $1",
                    order_id,
                )
            if milestone_id:
                await self.release_if_direct(milestone_id)
        return result

    async def _apply_gateway_status(
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

                if (
                    tx["status"] == "canceled"
                    and normalized in HOLD_STATUSES
                    and _parse_meta(tx["marketplace_meta"]).get("cancel_requested")
                ):
                    return await self._reopen_late_payment(conn, tx, payload, external_id, raw)
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
                        return await self._gateway_result(conn, tx, store_status)
                    # The milestone is already paid by another order (double click, a
                    # stale payment link) or no longer payable: this hold is a duplicate.
                    # It must never be released to the supplier — return it to the buyer.
                    logger.warning(
                        "Alif hold/ok but milestone %s is %s (order=%s) — duplicate, refunding",
                        tx["milestone_id"],
                        milestone["status"],
                        order_id,
                    )
                    meta["duplicate"] = True
                    await conn.execute(
                        """
                        UPDATE payment_gateway_transactions
                        SET marketplace_meta = $2::jsonb, updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        json.dumps(meta, ensure_ascii=False, default=str),
                    )
                    await log_event(
                        conn,
                        "payment.callback.duplicate",
                        "payment",
                        order_id,
                        actor_id=milestone["buyer_actor_id"],
                        details={
                            "order_id": order_id,
                            "milestone_id": tx["milestone_id"],
                            "contract_id": tx["contract_id"],
                            "amount": tx["amount"],
                            "milestone_status": milestone["status"],
                        },
                    )
                    result = await self._gateway_result(conn, tx, store_status)
                    result["refund_required"] = True
                    result["refund_reason"] = "Повторная оплата этапа"
                    return result

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
                    # A hold canceled by the bank (not by our own cancel request) means
                    # the buyer got the money back: the milestone is no longer funded.
                    if (
                        normalized == "canceled"
                        and tx["status"] in ("to_approve", "ok")
                        and not meta.get("cancel_requested")
                    ):
                        await self._unfund_milestone(conn, tx, order_id)
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

    async def _reopen_late_payment(self, conn, tx, payload: dict, external_id, raw: str) -> dict:
        """The buyer paid an order we had already canceled: keep the hold so it is refunded."""
        meta = _parse_meta(tx["marketplace_meta"])
        subs = payload.get("sub_transactions")
        if isinstance(subs, list):
            meta["sub_transactions"] = subs
        meta["late_payment"] = True
        await conn.execute(
            """
            UPDATE payment_gateway_transactions
            SET status = 'to_approve',
                external_transaction_id = COALESCE($2, external_transaction_id),
                raw_callback = $3::jsonb,
                marketplace_meta = $4::jsonb,
                paid_at = COALESCE(paid_at, NOW()),
                updated_at = NOW()
            WHERE id = $1
            """,
            tx["id"],
            external_id,
            raw,
            json.dumps(meta, ensure_ascii=False, default=str),
        )
        logger.warning(
            "Alif order=%s was paid after it had been canceled — refunding the hold",
            tx["order_id"],
        )
        await log_event(
            conn,
            "payment.callback.late",
            "payment",
            tx["order_id"],
            details={
                "order_id": tx["order_id"],
                "milestone_id": tx["milestone_id"],
                "contract_id": tx["contract_id"],
                "amount": tx["amount"],
                "before": {"status": "canceled"},
                "after": {"status": "to_approve"},
            },
        )
        result = await self._gateway_result(conn, tx, "to_approve")
        result["refund_required"] = True
        return result

    async def _set_milestone_commission(self, conn, row, commission: Decimal) -> None:
        await conn.execute(
            "UPDATE payment_milestones SET commission_amount = $2 WHERE id = $1",
            row["id"],
            commission,
        )
        await conn.execute(
            """
            UPDATE contracts
            SET commission_amount = (
                SELECT COALESCE(SUM(commission_amount), 0)
                FROM payment_milestones WHERE contract_id = $1
            )
            WHERE id = $1
            """,
            row["contract_id"],
        )
        logger.info(
            "Milestone %s commission adjusted to %s for Alif marketplace split",
            row["id"],
            commission,
        )

    async def _unfund_milestone(self, conn, tx, order_id: str) -> None:
        milestone = await self._load_milestone_contract(conn, tx["milestone_id"], for_update=True)
        if milestone["status"] != "funded":
            logger.warning(
                "Alif canceled order=%s but milestone %s is %s — left as is",
                order_id,
                milestone["id"],
                milestone["status"],
            )
            return
        still_held = await conn.fetchval(
            """
            SELECT 1 FROM payment_gateway_transactions
            WHERE milestone_id = $1 AND id <> $2 AND status IN ('to_approve', 'ok')
            LIMIT 1
            """,
            milestone["id"],
            tx["id"],
        )
        if still_held:
            return
        await conn.execute(
            "UPDATE payment_milestones SET status = 'awaiting_payment'::milestone_status WHERE id = $1",
            milestone["id"],
        )
        await log_event(
            conn,
            "payment.milestone.unfund",
            "milestone",
            milestone["id"],
            actor_id=milestone["buyer_actor_id"],
            details={
                "contract_id": milestone["contract_id"],
                "order_id": order_id,
                "amount": milestone["amount"],
                "source": "gateway_cancel",
                "before": {"status": "funded"},
                "after": {"status": "awaiting_payment"},
            },
        )
        # Funding activated the contract (pending_payment -> active); with no money
        # held any more the supplier must not be told to start the work.
        if milestone["contract_status"] != "active":
            return
        other_money = await conn.fetchval(
            """
            SELECT 1 FROM payment_milestones
            WHERE contract_id = $1 AND id <> $2
              AND status::text IN ('funded', 'released', 'disputed', 'in_progress',
                                   'submitted', 'approved')
            LIMIT 1
            """,
            milestone["contract_id"],
            milestone["id"],
        )
        if other_money:
            return
        await conn.execute(
            "UPDATE contracts SET status = 'pending_payment'::contract_status WHERE id = $1",
            milestone["contract_id"],
        )
        await log_event(
            conn,
            "contract.deactivate",
            "contract",
            milestone["contract_id"],
            actor_id=milestone["buyer_actor_id"],
            details={
                "milestone_id": milestone["id"],
                "source": "gateway_cancel",
                "before": {"status": "active"},
                "after": {"status": "pending_payment"},
            },
        )

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

    async def confirm_marketplace_delivery_for_milestone(self, milestone_id: int) -> bool:
        """Call Alif POST /confirm-delivery for held sub-transactions of one milestone."""
        return await self.confirm_marketplace_deliveries_for_milestones([milestone_id])

    async def confirm_marketplace_deliveries_for_milestones(
        self,
        milestone_ids: list[int],
        milestone_statuses: tuple[str, ...] = BUYER_RELEASABLE_STATUSES,
    ) -> bool:
        """Confirm held sub-transactions of the given milestones; True if the bank paid out.

        Only holds of milestones in ``milestone_statuses`` are released (frozen ones are
        not, unless an admin passes them explicitly), and never duplicate payments.
        """
        settings = get_settings()
        if not settings.alif_marketplace_enabled or not milestone_ids:
            return False

        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_TX_COLUMNS}
                WHERE t.milestone_id = ANY($1::bigint[])
                  AND t.gateway = $2
                  AND t.status = 'to_approve'
                  AND m.status::text = ANY($3::text[])
                  AND {NOT_DUPLICATE_SQL}
                ORDER BY t.id
                """,
                list(milestone_ids),
                GATEWAY_ALIF,
                list(milestone_statuses),
            )
        return await self._confirm_marketplace_rows(AlifClient(settings), rows) > 0

    async def confirm_marketplace_deliveries_for_contract(
        self,
        contract_id: int,
        milestone_statuses: tuple[str, ...] = BUYER_RELEASABLE_STATUSES,
    ) -> bool:
        """Confirm held marketplace sub-transactions of a contract; True if the bank paid out."""
        settings = get_settings()
        if not settings.alif_marketplace_enabled:
            return False

        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_TX_COLUMNS}
                WHERE t.contract_id = $1
                  AND t.gateway = $2
                  AND t.status = 'to_approve'
                  AND m.status::text = ANY($3::text[])
                  AND {NOT_DUPLICATE_SQL}
                ORDER BY t.id
                """,
                contract_id,
                GATEWAY_ALIF,
                list(milestone_statuses),
            )
        return await self._confirm_marketplace_rows(AlifClient(settings), rows) > 0

    async def _fetch_sub_transactions(self, client, order_id: str) -> list:
        """sub_transactions from checktxn (own copies), or [] when unavailable."""
        try:
            data = await client.check_transaction(order_id)
        except AlifGatewayError as exc:
            logger.warning(
                "Alif checktxn failed while fetching sub_transactions for order=%s: %s",
                order_id,
                exc.detail,
            )
            return []
        subs = data.get("sub_transactions") if isinstance(data, dict) else None
        if not isinstance(subs, list):
            return []
        # Own copies: per-sub flags set later must not leak into the bank response.
        return [dict(sub) for sub in subs if isinstance(sub, dict)]

    async def _confirm_marketplace_rows(self, client: AlifClient, rows) -> int:
        confirmed_rows = 0
        for row in rows:
            meta = _parse_meta(row["marketplace_meta"])
            subs = meta.get("sub_transactions") or []
            if not subs:
                logger.warning(
                    "No sub_transactions in meta for order=%s contract=%s — trying checktxn",
                    row["order_id"],
                    row["contract_id"],
                )
                subs = await self._fetch_sub_transactions(client, row["order_id"])
                if subs:
                    meta["sub_transactions"] = subs
                    await self._save_marketplace_meta(row["id"], meta)
            if not subs:
                raise ConflictError(
                    f"Alif: нет подтранзакций для заказа {row['order_id']}. "
                    "Банк не разделил платёж — подтверждение доставки невозможно. "
                    "Повторите после появления sub_transactions в checktxn."
                )

            amount_by_terminal = _terminal_amounts(row, meta)
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                sub_id = str(sub.get("transaction_id") or "")
                if not sub_id or sub.get("confirmed"):
                    continue
                amount = _sub_amount(row, sub, amount_by_terminal, len(subs))
                await client.confirm_delivery(
                    transaction_id=sub_id, amount=amount, order_id=row["order_id"]
                )
                # Persist per sub-transaction: a retry after a partial failure must not
                # confirm the already confirmed ones again.
                sub["confirmed"] = True
                await self._save_marketplace_meta(row["id"], meta)

            async with self.connection() as conn:
                await conn.execute(
                    """
                    UPDATE payment_gateway_transactions
                    SET status = 'ok', updated_at = NOW()
                    WHERE id = $1 AND status = 'to_approve'
                    """,
                    row["id"],
                )
            confirmed_rows += 1
        return confirmed_rows

    async def cancel_marketplace_for_contract(
        self, contract_id: int, reason: str = "Отмена сделки"
    ) -> None:
        """Cancel Alif marketplace holds of a contract that were not released.

        Money of milestones already released to the supplier is never canceled.
        Every transaction is attempted. Transactions whose sub-transactions were all
        canceled are marked canceled locally; failed ones stay untouched and an
        AlifGatewayError is raised after all transactions were processed.
        """
        settings = get_settings()
        if not settings.alif_marketplace_enabled:
            return

        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_TX_COLUMNS}
                WHERE t.contract_id = $1
                  AND t.gateway = $2
                  AND t.status IN ('to_approve', 'pending', 'ok')
                  AND m.status::text <> 'released'
                ORDER BY t.id
                """,
                contract_id,
                GATEWAY_ALIF,
            )
        await self._cancel_marketplace_rows(
            AlifClient(settings) if rows else None, rows, reason, f"сделки #{contract_id}"
        )

    async def cancel_marketplace_for_milestones(
        self, milestone_ids: list[int], reason: str = "Возврат покупателю"
    ) -> None:
        """Cancel Alif marketplace holds of the given milestones (refund to the buyer)."""
        settings = get_settings()
        if not settings.alif_marketplace_enabled or not milestone_ids:
            return

        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_TX_COLUMNS}
                WHERE t.milestone_id = ANY($1::bigint[])
                  AND t.gateway = $2
                  AND t.status IN ('to_approve', 'pending', 'ok')
                ORDER BY t.id
                """,
                list(milestone_ids),
                GATEWAY_ALIF,
            )
        await self._cancel_marketplace_rows(
            AlifClient(settings) if rows else None, rows, reason, "этапов"
        )

    async def _refund_late_payment(
        self, order_id: str, reason: str = "Сделка отменена до оплаты"
    ) -> None:
        """Return a hold that must not be kept: paid after cancel, or a duplicate payment."""
        settings = get_settings()
        if not settings.alif_marketplace_enabled:
            logger.error(
                "Alif order=%s was paid after cancellation; refund it manually", order_id
            )
            return
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"SELECT {_TX_COLUMNS} WHERE t.order_id = $1 AND t.status = 'to_approve'",
                order_id,
            )
        try:
            await self._cancel_marketplace_rows(
                AlifClient(settings) if rows else None,
                rows,
                reason,
                f"заказа {order_id}",
            )
        except AlifGatewayError as exc:
            logger.error(
                "Alif order=%s paid after cancellation could not be refunded: %s",
                order_id,
                exc.detail,
            )

    async def _cancel_marketplace_rows(self, client, rows, reason: str, label: str) -> None:
        failed_orders: list[str] = []
        for row in rows:
            meta = _parse_meta(row["marketplace_meta"])
            # Mark before calling the bank: the cancel callback for our own request
            # must not reopen the milestone for payment.
            meta["cancel_requested"] = True
            await self._save_marketplace_meta(row["id"], meta)
            subs = meta.get("sub_transactions") or []
            if not subs:
                # The buyer may have paid without the callback reaching us yet.
                subs = await self._fetch_sub_transactions(client, row["order_id"])
                if subs:
                    meta["sub_transactions"] = subs
            amount_by_terminal = _terminal_amounts(row, meta)
            row_failed = False
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                sub_id = str(sub.get("transaction_id") or "")
                if not sub_id or sub.get("canceled"):
                    continue
                if sub.get("confirmed"):
                    row_failed = True
                    logger.warning(
                        "Alif sub=%s of order=%s is already paid out — not canceled",
                        sub_id,
                        row["order_id"],
                    )
                    continue
                try:
                    amount = _sub_amount(row, sub, amount_by_terminal, len(subs))
                    await client.cancel_marketplace(
                        transaction_id=sub_id,
                        amount=amount,
                        reason=reason,
                        order_id=row["order_id"],
                    )
                    sub["canceled"] = True
                except (AlifGatewayError, ConflictError) as exc:
                    row_failed = True
                    logger.warning(
                        "Alif cancel failed for order=%s sub=%s: %s",
                        row["order_id"],
                        sub_id,
                        exc.detail,
                    )
            if row_failed:
                # The hold is still alive: a later cancel by the bank itself must
                # unfund the milestone again.
                meta.pop("cancel_requested", None)
            await self._save_marketplace_meta(row["id"], meta)

            if row_failed:
                failed_orders.append(row["order_id"])
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
                f"Alif: не удалось отменить платежи {label} ({', '.join(failed_orders)})"
            )

    async def _save_marketplace_meta(self, tx_id: int, meta: dict) -> None:
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE payment_gateway_transactions
                SET marketplace_meta = $2::jsonb, updated_at = NOW()
                WHERE id = $1
                """,
                tx_id,
                json.dumps(meta, ensure_ascii=False, default=str),
            )
