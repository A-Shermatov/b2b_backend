from types import SimpleNamespace

from app.core.exceptions import ConflictError, ForbiddenError
from app.modules.contracts.services import ContractService
from app.modules.finance.services.commission import ensure_contract_amount_allowed
from app.modules.notifications.services import NotificationService
from app.modules.proposals.schemas import ProposalUpdate
from app.modules.proposals.services.common import (
    ACCEPTABLE_RFQ_STATUSES,
    BUYER_REVIEWABLE_PROPOSAL_STATUSES,
    ensure_not_self_dealing,
    load_proposal_context,
    load_proposal_row,
    proposal_to_response,
)
from app.modules.rfqs.services.common import rfq_deadline_passed
from app.modules.settings.service import get_platform_settings
from app.enums import PaymentType
from app.utils.audit import log_event


async def _log_transition(conn, action, row, actor_id, after_status, **extra):
    await log_event(
        conn,
        action,
        "proposal",
        row["id"],
        actor_id=actor_id,
        details={
            "rfq_id": row["rfq_id"],
            "before": {"status": row["status"]},
            "after": {"status": after_status},
            **extra,
        },
    )


class ProposalUpdateMixin:
    async def update_proposal(self, proposal_id: int, supplier_id: int, data: ProposalUpdate):
        async with self.connection() as conn:
            row = await load_proposal_row(conn, proposal_id)
            if row["supplier_actor_id"] != supplier_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "submitted":
                raise ConflictError("Proposal cannot be edited")
            if await rfq_deadline_passed(conn, row["rfq_id"]):
                raise ConflictError("Срок приёма предложений истёк")
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
            changes = {
                key: value
                for key, value in data.model_dump(exclude_none=True).items()
                if key in ("price", "currency", "delivery_time", "message")
            }
            await log_event(
                conn,
                "proposal.update",
                "proposal",
                proposal_id,
                actor_id=supplier_id,
                details={
                    "rfq_id": row["rfq_id"],
                    "before": {key: row[key] for key in changes if key != "message"},
                    "after": {key: value for key, value in changes.items() if key != "message"},
                    "message_changed": "message" in changes,
                },
            )
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def withdraw(self, proposal_id: int, supplier_id: int):
        async with self.connection() as conn:
            row = await load_proposal_row(conn, proposal_id)
            if row["supplier_actor_id"] != supplier_id:
                raise ForbiddenError("Access denied")
            if row["status"] == "pending_supplier":
                raise ConflictError("Buyer-initiated offer cannot be withdrawn by supplier")
            if row["status"] not in ("submitted", "viewed", "shortlisted"):
                raise ConflictError("Proposal cannot be withdrawn")
            await conn.execute(
                "UPDATE proposals SET status = 'withdrawn'::proposal_status WHERE id = $1",
                proposal_id,
            )
            await _log_transition(conn, "proposal.withdraw", row, supplier_id, "withdrawn")
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def buyer_withdraw_offer(self, proposal_id: int, buyer_actor_id: int):
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            if row["rfq_actor_id"] != buyer_actor_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "pending_supplier":
                raise ConflictError("Only pending catalog offers can be withdrawn")
            await conn.execute(
                "UPDATE proposals SET status = 'withdrawn'::proposal_status WHERE id = $1",
                proposal_id,
            )
            await _log_transition(
                conn, "proposal.offer_withdraw", row, buyer_actor_id, "withdrawn"
            )
            await conn.execute(
                """
                UPDATE rfqs
                SET status = 'cancelled'::rfq_status, updated_at = NOW()
                WHERE id = $1 AND status::text = 'receiving_proposals'
                """,
                row["rfq_id"],
            )
            withdrawn = proposal_to_response(await load_proposal_row(conn, proposal_id))
        await NotificationService().notify_actor_members(
            row["supplier_actor_id"],
            "proposal",
            "Запрос отозван",
            f"Заказчик отозвал запрос по заявке «{row['rfq_title']}»",
            f"/supplier/rfqs/{row['rfq_id']}",
        )
        return withdrawn

    async def shortlist(self, proposal_id: int, buyer_actor_id: int):
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            if row["rfq_actor_id"] != buyer_actor_id:
                raise ForbiddenError("Access denied")
            if row["status"] not in BUYER_REVIEWABLE_PROPOSAL_STATUSES:
                raise ConflictError("Proposal cannot be shortlisted")
            await conn.execute(
                "UPDATE proposals SET status = 'shortlisted'::proposal_status WHERE id = $1",
                proposal_id,
            )
            await _log_transition(conn, "proposal.shortlist", row, buyer_actor_id, "shortlisted")
            return proposal_to_response(await load_proposal_row(conn, proposal_id))

    async def reject(self, proposal_id: int, buyer_actor_id: int):
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            if row["rfq_actor_id"] != buyer_actor_id:
                raise ForbiddenError("Access denied")
            if row["status"] not in BUYER_REVIEWABLE_PROPOSAL_STATUSES:
                raise ConflictError("Proposal cannot be rejected")
            await conn.execute(
                "UPDATE proposals SET status = 'rejected'::proposal_status WHERE id = $1",
                proposal_id,
            )
            await _log_transition(conn, "proposal.reject", row, buyer_actor_id, "rejected")
            rejected = proposal_to_response(await load_proposal_row(conn, proposal_id))
        await NotificationService().notify_actor_members(
            row["supplier_actor_id"],
            "proposal",
            "Предложение отклонено",
            f"Заказчик отклонил ваше предложение по заявке «{row['rfq_title']}»",
            f"/supplier/rfqs/{row['rfq_id']}",
        )
        return rejected

    async def supplier_reject(self, proposal_id: int, supplier_id: int):
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            if row["supplier_actor_id"] != supplier_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "pending_supplier":
                raise ConflictError("Only pending catalog offers can be rejected")
            await conn.execute(
                "UPDATE proposals SET status = 'rejected'::proposal_status WHERE id = $1",
                proposal_id,
            )
            await _log_transition(
                conn, "proposal.supplier_reject", row, supplier_id, "rejected"
            )
            await conn.execute(
                """
                UPDATE rfqs
                SET status = 'cancelled'::rfq_status, updated_at = NOW()
                WHERE id = $1 AND status::text = 'receiving_proposals'
                """,
                row["rfq_id"],
            )
            rejected = proposal_to_response(await load_proposal_row(conn, proposal_id))
        await NotificationService().notify_actor_members(
            row["rfq_actor_id"],
            "proposal",
            "Запрос отклонён",
            f"Исполнитель отклонил ваш запрос по «{row['rfq_title']}»",
            f"/customer/rfqs/{row['rfq_id']}",
        )
        return rejected

    async def supplier_accept(self, proposal_id: int, supplier_id: int):
        async with self.connection() as conn:
            async with conn.transaction():
                row = await load_proposal_context(conn, proposal_id)
                if row["supplier_actor_id"] != supplier_id:
                    raise ForbiddenError("Access denied")
                if row["status"] != "pending_supplier":
                    raise ConflictError("Only pending catalog offers can be accepted")
                await ensure_not_self_dealing(
                    conn, row["rfq_actor_id"], row["supplier_actor_id"]
                )
                ensure_contract_amount_allowed(
                    row["price"],
                    row["currency"],
                    await get_platform_settings(conn, use_cache=False),
                )
                await conn.execute(
                    "UPDATE proposals SET status = 'accepted'::proposal_status WHERE id = $1",
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
                    row["rfq_actor_id"],
                    payment_type=PaymentType.full_postpayment,
                    milestones=None,
                    buyer_user_id=None,
                )
                await _log_transition(
                    conn,
                    "proposal.supplier_accept",
                    row,
                    supplier_id,
                    "accepted",
                    contract_id=contract["id"],
                )
                await conn.execute(
                    "UPDATE rfqs SET status = 'contract_created'::rfq_status WHERE id = $1",
                    row["rfq_id"],
                )
                updated = await load_proposal_row(conn, proposal_id)
                contract_id = contract["id"]

        await NotificationService().notify_actor_members(
            row["rfq_actor_id"],
            "contract",
            "Исполнитель принял запрос",
            f"По заявке «{row['rfq_title']}» открыта сделка",
            f"/customer/contracts/{contract_id}",
        )
        return {"proposal": proposal_to_response(updated), "contract_id": contract_id}

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
                await conn.fetchval(
                    "SELECT id FROM rfqs WHERE id = $1 FOR UPDATE", row["rfq_id"]
                )
                row = await load_proposal_context(conn, proposal_id)
                if row["status"] == "pending_supplier":
                    raise ConflictError("Catalog offer must be accepted by the supplier")
                if row["status"] not in BUYER_REVIEWABLE_PROPOSAL_STATUSES:
                    raise ConflictError("Proposal already finalized")
                if row["rfq_status"] not in ACCEPTABLE_RFQ_STATUSES:
                    raise ConflictError("RFQ is not accepting proposals")
                if await conn.fetchval(
                    "SELECT 1 FROM contracts WHERE rfq_id = $1 LIMIT 1", row["rfq_id"]
                ):
                    raise ConflictError("RFQ already has a contract")
                await ensure_not_self_dealing(
                    conn, buyer_actor_id, row["supplier_actor_id"]
                )
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
                await _log_transition(
                    conn,
                    "proposal.accept",
                    row,
                    buyer_actor_id,
                    "accepted",
                    contract_id=contract["id"],
                    price=row["price"],
                    currency=row["currency"],
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
