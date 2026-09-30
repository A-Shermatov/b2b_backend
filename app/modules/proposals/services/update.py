from types import SimpleNamespace

from app.core.exceptions import ConflictError, ForbiddenError
from app.modules.contracts.services import ContractService
from app.modules.finance.services.commission import ensure_contract_amount_allowed
from app.modules.notifications.services import NotificationService
from app.modules.proposals.schemas import ProposalUpdate
from app.modules.proposals.services.common import (
    FINAL_PROPOSAL_STATUSES,
    load_proposal_context,
    load_proposal_row,
    proposal_to_response,
)
from app.modules.settings.service import get_platform_settings
from app.enums import PaymentType


class ProposalUpdateMixin:
    async def update_proposal(self, proposal_id: int, supplier_id: int, data: ProposalUpdate):
        async with self.connection() as conn:
            row = await load_proposal_row(conn, proposal_id)
            if row["supplier_actor_id"] != supplier_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "submitted":
                raise ConflictError("Proposal cannot be edited")
            if data.price is not None or data.currency is not None:
                ensure_contract_amount_allowed(
                    data.price if data.price is not None else row["price"],
                    data.currency if data.currency is not None else row["currency"],
                    await get_platform_settings(conn),
                )
            sets = []
            params: list = []
            if data.price is not None:
                params.append(data.price)
                sets.append(f"price = ${len(params)}")
            if data.currency is not None:
                params.append(data.currency)
                sets.append(f"currency = ${len(params)}::currency")
            if data.delivery_time is not None:
                params.append(data.delivery_time)
                sets.append(f"delivery_time = ${len(params)}")
            if data.message is not None:
                params.append(data.message)
                sets.append(f"message = ${len(params)}")
            if sets:
                params.append(proposal_id)
                await conn.execute(
                    f"UPDATE proposals SET {', '.join(sets)} WHERE id = ${len(params)}",
                    *params,
                )
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def withdraw(self, proposal_id: int, supplier_id: int):
        async with self.connection() as conn:
            row = await load_proposal_row(conn, proposal_id)
            if row["supplier_actor_id"] != supplier_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                "UPDATE proposals SET status = 'withdrawn'::proposal_status WHERE id = $1",
                proposal_id,
            )
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def shortlist(self, proposal_id: int, buyer_actor_id: int):
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            if row["rfq_actor_id"] != buyer_actor_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                "UPDATE proposals SET status = 'shortlisted'::proposal_status WHERE id = $1",
                proposal_id,
            )
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def reject(self, proposal_id: int, buyer_actor_id: int):
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            if row["rfq_actor_id"] != buyer_actor_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                "UPDATE proposals SET status = 'rejected'::proposal_status WHERE id = $1",
                proposal_id,
            )
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def accept(
        self,
        proposal_id: int,
        buyer_actor_id: int,
        payment_type: PaymentType | None = None,
        milestones: list[dict] | None = None,
        buyer_user_id: int | None = None,
    ):
        async with self.connection() as conn:
            async with conn.transaction():
                row = await load_proposal_context(conn, proposal_id)
                if row["rfq_actor_id"] != buyer_actor_id:
                    raise ForbiddenError("Access denied")
                if row["status"] in FINAL_PROPOSAL_STATUSES:
                    raise ConflictError("Proposal already finalized")
                ensure_contract_amount_allowed(
                    row["price"], row["currency"], await get_platform_settings(conn, use_cache=False)
                )
                await conn.execute(
                    "UPDATE proposals SET status = 'accepted'::proposal_status WHERE id = $1",
                    proposal_id,
                )
                await conn.execute(
                    """
                    UPDATE proposals
                    SET status = 'archived'::proposal_status
                    WHERE rfq_id = $1
                      AND id <> $2
                      AND status::text NOT IN ('rejected', 'withdrawn', 'archived')
                    """,
                    row["rfq_id"],
                    proposal_id,
                )
                await conn.execute(
                    "UPDATE rfqs SET status = 'supplier_selected'::rfq_status WHERE id = $1",
                    row["rfq_id"],
                )
                contract = await ContractService(conn).create_from_proposal(
                    {
                        "id": row["id"],
                        "supplier_actor_id": row["supplier_actor_id"],
                        "price": row["price"],
                        "currency": row["currency"],
                        "delivery_time": row["delivery_time"],
                        "rfq_id": row["rfq_id"],
                        "rfq": {
                            "id": row["rfq_id"],
                            "title": row["rfq_title"],
                            "description": row["rfq_description"],
                            "deadline": row["rfq_deadline"],
                        },
                    },
                    buyer_actor_id,
                    payment_type=payment_type,
                    milestones=milestones,
                    buyer_user_id=buyer_user_id,
                )
                await conn.execute(
                    "UPDATE rfqs SET status = 'contract_created'::rfq_status WHERE id = $1",
                    row["rfq_id"],
                )
                updated = await load_proposal_row(conn, proposal_id)
                contract_id = contract["id"]

        await NotificationService().notify_supplier_on_accept(
            SimpleNamespace(
                supplier_actor_id=row["supplier_actor_id"],
                rfq=SimpleNamespace(id=row["rfq_id"], title=row["rfq_title"]),
            ),
            contract_id,
        )
        return {"proposal": proposal_to_response(updated), "contract_id": contract_id}
