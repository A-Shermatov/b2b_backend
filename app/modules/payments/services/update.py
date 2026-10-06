from app.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.payments.gateways.alif import AlifGatewayError

FUNDABLE_MILESTONE_STATUSES = ("pending", "awaiting_payment")
ALIF_NOT_CONFIGURED = (
    "Alif не настроен: задайте ALIF_TERMINAL_KEY и ALIF_TERMINAL_PASSWORD"
)


def ensure_alif_configured() -> bool:
    settings = get_settings()
    if settings.alif_requested and not settings.alif_enabled:
        raise AlifGatewayError(ALIF_NOT_CONFIGURED)
    return settings.alif_enabled


class PaymentUpdateMixin:
    async def _load_milestone_contract(self, conn, milestone_id: int, for_update: bool = False):
        row = await conn.fetchrow(
            f"""
            SELECT
                m.id, m.contract_id, m.title, m.amount, m.status::text AS status,
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

    async def _apply_milestone_funded(self, conn, row) -> None:
        await conn.execute(
            "UPDATE payment_milestones SET status = 'funded'::milestone_status WHERE id = $1",
            row["id"],
        )
        if row["contract_status"] == "pending_payment":
            await conn.execute(
                "UPDATE contracts SET status = 'active'::contract_status WHERE id = $1",
                row["contract_id"],
            )

    async def fund_milestone(
        self, milestone_id: int, buyer_id: int, idempotency_key: str | None = None
    ):
        if ensure_alif_configured():
            return await self.start_alif_payment(milestone_id, buyer_id)

        async with self.connection() as conn:
            async with conn.transaction():
                row = await self._load_milestone_contract(conn, milestone_id, for_update=True)
                if row["buyer_actor_id"] != buyer_id:
                    raise ForbiddenError("Access denied")
                if row["status"] == "funded":
                    return {"id": row["id"], "status": row["status"]}
                if row["status"] not in FUNDABLE_MILESTONE_STATUSES:
                    raise ConflictError("Milestone cannot be funded")
                await self._apply_milestone_funded(conn, row)
        return {"id": milestone_id, "status": "funded"}

    async def approve_milestone(self, milestone_id: int, buyer_id: int):
        from app.modules.finance.services.commission import record_milestone_release

        async with self.connection() as conn:
            async with conn.transaction():
                row = await self._load_milestone_contract(conn, milestone_id)
                if row["buyer_actor_id"] != buyer_id:
                    raise ForbiddenError("Access denied")
                if row["status"] != "funded":
                    raise ConflictError("Milestone must be funded first")
                await conn.execute(
                    "UPDATE payment_milestones SET status = 'released'::milestone_status WHERE id = $1",
                    milestone_id,
                )
                await record_milestone_release(conn, milestone_id)
        return {"id": milestone_id, "status": "released"}

    async def mock_confirm_payment(self, milestone_id: int, buyer_id: int) -> dict:
        if ensure_alif_configured():
            raise ConflictError("Mock payments are disabled while Alif is enabled")
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
