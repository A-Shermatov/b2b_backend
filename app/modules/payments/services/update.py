import logging

from app.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.payments.gateways.alif import AlifGatewayError
from app.utils.audit import log_event

logger = logging.getLogger(__name__)

FUNDABLE_MILESTONE_STATUSES = ("pending", "awaiting_payment")
CLOSED_CONTRACT_STATUSES = ("cancelled", "completed", "disputed")
ACTIVE_DISPUTE_STATUSES = ("open", "under_review", "appealed")
ALIF_NOT_CONFIGURED = (
    "Alif не настроен: задайте ALIF_TERMINAL_KEY и ALIF_TERMINAL_PASSWORD"
)
GATEWAY_NOT_CONFIGURED = "Платёжный шлюз не настроен"


def ensure_alif_configured() -> bool:
    settings = get_settings()
    if settings.alif_requested and not settings.alif_enabled:
        raise AlifGatewayError(ALIF_NOT_CONFIGURED)
    return settings.alif_enabled


def ensure_mock_payments_allowed() -> None:
    if not get_settings().allow_mock_payments:
        raise ConflictError(GATEWAY_NOT_CONFIGURED)


def ensure_contract_fundable(row) -> None:
    # Work accepted before it was paid (older postpayment deals) must still be payable
    # and releasable, otherwise the supplier is never paid.
    if row["contract_status"] == "completed" and row["status"] in ("awaiting_payment", "funded"):
        return
    if row["contract_status"] in CLOSED_CONTRACT_STATUSES:
        raise ConflictError("Contract does not accept payments in its current status")


async def ensure_no_active_dispute(conn, contract_id: int) -> None:
    has_dispute = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM disputes
            WHERE contract_id = $1 AND status::text = ANY($2::text[])
        )
        """,
        contract_id,
        list(ACTIVE_DISPUTE_STATUSES),
    )
    if has_dispute:
        raise ConflictError("Contract is under dispute")


class PaymentUpdateMixin:
    async def _load_milestone_contract(self, conn, milestone_id: int, for_update: bool = False):
        row = await conn.fetchrow(
            f"""
            SELECT
                m.id, m.contract_id, m.title, m.amount,
                COALESCE(m.commission_amount, 0) AS commission_amount,
                m.status::text AS status,
                c.buyer_actor_id, c.supplier_actor_id, c.status::text AS contract_status,
                c.currency::text AS currency
            FROM payment_milestones m
            JOIN contracts c ON c.id = m.contract_id
            WHERE m.id = $1
            {"FOR UPDATE OF m" if for_update else ""}
            """,
            milestone_id,
        )
        if not row:
            raise NotFoundError("Milestone not found")
        return row

    async def _apply_milestone_funded(self, conn, row, source: str = "mock") -> None:
        await conn.execute(
            "UPDATE payment_milestones SET status = 'funded'::milestone_status WHERE id = $1",
            row["id"],
        )
        await log_event(
            conn,
            "payment.milestone.fund",
            "milestone",
            row["id"],
            actor_id=row["buyer_actor_id"],
            details={
                "contract_id": row["contract_id"],
                "amount": row["amount"],
                "currency": row["currency"],
                "source": source,
                "before": {"status": row["status"]},
                "after": {"status": "funded"},
            },
        )
        if row["contract_status"] == "pending_payment":
            await conn.execute(
                "UPDATE contracts SET status = 'active'::contract_status WHERE id = $1",
                row["contract_id"],
            )
            await log_event(
                conn,
                "contract.activate",
                "contract",
                row["contract_id"],
                actor_id=row["buyer_actor_id"],
                details={
                    "milestone_id": row["id"],
                    "before": {"status": "pending_payment"},
                    "after": {"status": "active"},
                },
            )

    async def fund_milestone(
        self, milestone_id: int, buyer_id: int, idempotency_key: str | None = None
    ):
        if ensure_alif_configured():
            return await self.start_alif_payment(milestone_id, buyer_id)
        ensure_mock_payments_allowed()

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
                await self._apply_milestone_funded(conn, row)
        if await self.release_if_direct(milestone_id):
            return {"id": milestone_id, "status": "released"}
        return {"id": milestone_id, "status": "funded"}

    async def release_if_direct(self, milestone_id: int) -> bool:
        """Pay the supplier at once when the buyer chose to pay without the bank hold.

        Runs after the payment is recorded. A failed payout leaves the milestone
        funded, so acceptance of the work still releases it the usual way.
        """
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT m.status::text AS status, c.payment_mode, c.buyer_actor_id
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                WHERE m.id = $1
                """,
                milestone_id,
            )
        if not row or row["payment_mode"] != "direct" or row["status"] != "funded":
            return False
        try:
            result = await self.approve_milestone(milestone_id, row["buyer_actor_id"])
        except Exception:
            logger.exception(
                "Direct payout of milestone %s failed; it stays funded", milestone_id
            )
            return False
        return result.get("status") == "released"

    async def _ensure_milestone_approvable(self, conn, row, buyer_id: int) -> None:
        if row["buyer_actor_id"] != buyer_id:
            raise ForbiddenError("Access denied")
        if row["status"] != "funded":
            raise ConflictError("Milestone must be funded first")
        ensure_contract_fundable(row)
        await ensure_no_active_dispute(conn, row["contract_id"])

    async def approve_milestone(self, milestone_id: int, buyer_id: int):
        from app.utils.locks import contract_lock

        async with self.connection() as conn:
            row = await self._load_milestone_contract(conn, milestone_id)
            if row["buyer_actor_id"] != buyer_id:
                raise ForbiddenError("Access denied")
        # Bank payout happens between two DB checks: lock the deal for the whole call.
        async with contract_lock(row["contract_id"]):
            return await self._approve_milestone_locked(milestone_id, buyer_id)

    async def _approve_milestone_locked(self, milestone_id: int, buyer_id: int):
        from app.modules.finance.services.commission import record_milestone_release

        async with self.connection() as conn:
            row = await self._load_milestone_contract(conn, milestone_id)
            await self._ensure_milestone_approvable(conn, row, buyer_id)

        paid_out = await self.confirm_marketplace_delivery_for_milestone(milestone_id)

        async with self.connection() as conn:
            async with conn.transaction():
                row = await self._load_milestone_contract(conn, milestone_id, for_update=True)
                if not paid_out:
                    await self._ensure_milestone_approvable(conn, row, buyer_id)
                # The bank has released the money, so only re-check what could make
                # recording the release wrong: a dispute opened in the meantime must
                # not leave the milestone "funded" while the supplier already got paid.
                elif row["buyer_actor_id"] != buyer_id:
                    raise ForbiddenError("Access denied")
                elif row["status"] == "released":
                    return {"id": milestone_id, "status": "released"}
                elif row["status"] != "funded":
                    logger.error(
                        "Milestone %s became %s while its payout was confirmed in the bank",
                        milestone_id,
                        row["status"],
                    )
                    raise ConflictError("Milestone must be funded first")
                await conn.execute(
                    "UPDATE payment_milestones SET status = 'released'::milestone_status WHERE id = $1",
                    milestone_id,
                )
                await record_milestone_release(conn, milestone_id)
                await log_event(
                    conn,
                    "payment.milestone.release",
                    "milestone",
                    milestone_id,
                    actor_id=buyer_id,
                    details={
                        "contract_id": row["contract_id"],
                        "amount": row["amount"],
                        "currency": row["currency"],
                        "before": {"status": row["status"]},
                        "after": {"status": "released"},
                    },
                )
        return {"id": milestone_id, "status": "released"}

    async def mock_confirm_payment(self, milestone_id: int, buyer_id: int) -> dict:
        if ensure_alif_configured():
            raise ConflictError("Mock payments are disabled while Alif is enabled")
        ensure_mock_payments_allowed()
        async with self.connection() as conn:
            row = await self._load_milestone_contract(conn, milestone_id)
            if row["buyer_actor_id"] != buyer_id:
                raise ForbiddenError("Access denied")
            if row["status"] == "funded":
                return {
                    "status": "confirmed",
                    "milestone_id": row["id"],
                    "milestone_status": row["status"],
                }
        milestone = await self.fund_milestone(milestone_id, buyer_id)
        return {
            "status": "confirmed",
            "milestone_id": milestone["id"],
            "milestone_status": milestone["status"],
        }
