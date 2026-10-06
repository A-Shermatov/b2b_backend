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
    format_amount,
)
from app.modules.payments.services.update import FUNDABLE_MILESTONE_STATUSES

logger = logging.getLogger(__name__)

GATEWAY_ALIF = "alif"
FINAL_STATUSES = ("ok", "failed", "canceled")
PENDING_REUSE_MINUTES = 30


class InvalidCallbackSignature(Exception):
    pass


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
                order_id = f"MS{milestone_id}-{secrets.token_hex(6)}"
                await conn.execute(
                    """
                    INSERT INTO payment_gateway_transactions
                        (order_id, gateway, milestone_id, contract_id, buyer_actor_id, amount, currency)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    order_id,
                    GATEWAY_ALIF,
                    milestone_id,
                    row["contract_id"],
                    buyer_id,
                    amount,
                    row["currency"],
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
        logger.info("Alif payment created: order=%s milestone=%s", order_id, milestone_id)
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

        async with self.connection() as conn:
            async with conn.transaction():
                tx = await conn.fetchrow(
                    """
                    SELECT id, order_id, milestone_id, contract_id, amount, status
                    FROM payment_gateway_transactions
                    WHERE order_id = $1
                    FOR UPDATE
                    """,
                    order_id,
                )
                if not tx:
                    raise NotFoundError("Payment order not found")

                if tx["status"] == status or tx["status"] == "canceled":
                    return await self._gateway_result(conn, tx, tx["status"])
                if tx["status"] == "ok" and status != "canceled":
                    return await self._gateway_result(conn, tx, tx["status"])
                if tx["status"] == "failed" and status != "ok":
                    return await self._gateway_result(conn, tx, tx["status"])

                if status == "ok":
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

                    await conn.execute(
                        """
                        UPDATE payment_gateway_transactions
                        SET status = 'ok',
                            external_transaction_id = COALESCE($2, external_transaction_id),
                            transaction_type = COALESCE($3, transaction_type),
                            account_masked = COALESCE($4, account_masked),
                            raw_callback = $5::jsonb,
                            paid_at = NOW(),
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        external_id,
                        payload.get("transaction_type") or None,
                        payload.get("account") or None,
                        raw,
                    )
                    milestone = await self._load_milestone_contract(
                        conn, tx["milestone_id"], for_update=True
                    )
                    if milestone["status"] in FUNDABLE_MILESTONE_STATUSES:
                        await self._apply_milestone_funded(conn, milestone)
                    else:
                        logger.warning(
                            "Alif payment ok but milestone %s is %s (order=%s): manual refund may be needed",
                            tx["milestone_id"],
                            milestone["status"],
                            order_id,
                        )
                    return await self._gateway_result(conn, tx, "ok")

                if status in ("failed", "canceled"):
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
                        status,
                        external_id,
                        raw,
                    )
                    if status == "canceled":
                        logger.warning(
                            "Alif payment canceled: order=%s milestone=%s", order_id, tx["milestone_id"]
                        )
                    return await self._gateway_result(conn, tx, status)

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

    async def handle_alif_callback(self, payload: dict[str, Any]) -> dict:
        if not AlifClient().verify_callback(payload):
            raise InvalidCallbackSignature()

        event = payload.get("event") or "payment"
        if event == "payment":
            order_id = str(payload.get("orderId", ""))
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

        if event == "cancel":
            transaction_id = str(payload.get("transaction_id", ""))
            async with self.connection() as conn:
                order_id = await conn.fetchval(
                    """
                    SELECT order_id FROM payment_gateway_transactions
                    WHERE external_transaction_id = $1 AND gateway = $2
                    """,
                    transaction_id,
                    GATEWAY_ALIF,
                )
            if not order_id:
                logger.warning("Alif cancel callback for unknown transaction=%s", transaction_id)
                return {"transaction_id": transaction_id, "ignored": True}
            return await self.apply_gateway_status(
                order_id, str(payload.get("status", "")), transaction_id, payload
            )

        logger.info("Alif callback with unsupported event=%s ignored", event)
        return {"ignored": True}
