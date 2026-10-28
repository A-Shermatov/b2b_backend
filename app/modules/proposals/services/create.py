from app.core.exceptions import ConflictError, ForbiddenError
from app.modules.finance.services.commission import ensure_contract_amount_allowed
from app.modules.notifications.services import NotificationService
from app.modules.proposals.schemas import ProposalCreate, ProposalMessageCreate
from app.modules.proposals.services.common import (
    OPEN_CHAT_STATUSES,
    assert_chat_access,
    ensure_not_self_dealing,
    load_proposal_context,
    load_proposal_row,
    message_to_response,
    proposal_to_response,
)
from app.modules.rfqs.services.common import (
    expire_overdue_rfqs,
    fetch_invites_map,
    load_rfq_row,
    rfq_deadline_passed,
)
from app.modules.settings.service import get_platform_settings


class ProposalCreateMixin:
    async def submit(self, rfq_id: str, supplier_id: int, data: ProposalCreate):
        async with self.connection() as conn:
            await expire_overdue_rfqs(conn)
            rfq = await load_rfq_row(conn, rfq_id)
            if rfq["status"] == "expired" or (
                rfq["status"] in ("published", "receiving_proposals")
                and await rfq_deadline_passed(conn, rfq_id)
            ):
                raise ConflictError("Срок приёма предложений истёк")
            if rfq["status"] not in ("published", "receiving_proposals"):
                raise ConflictError("RFQ is not accepting proposals")
            await ensure_not_self_dealing(conn, rfq["actor_id"], supplier_id)
            if rfq["visibility"] == "invited_only":
                invites = await fetch_invites_map(conn, [rfq_id])
                if supplier_id not in invites.get(rfq_id, []):
                    raise ForbiddenError("Not invited to this RFQ")
            existing = await conn.fetchrow(
                """
                SELECT id FROM proposals
                WHERE rfq_id = $1 AND supplier_actor_id = $2
                """,
                rfq_id,
                supplier_id,
            )
            if existing:
                raise ConflictError("Proposal already submitted")
            ensure_contract_amount_allowed(
                data.price, data.currency, await get_platform_settings(conn)
            )
            row = await conn.fetchrow(
                """
                INSERT INTO proposals (
                    rfq_id, supplier_actor_id, price, currency, delivery_time,
                    message, status, created_at
                )
                VALUES (
                    $1, $2, $3, $4::currency, $5, $6, 'submitted'::proposal_status, NOW()
                )
                RETURNING id
                """,
                rfq_id,
                supplier_id,
                data.price,
                data.currency,
                data.delivery_time,
                data.message,
            )
            created = await load_proposal_row(conn, row["id"])
        await NotificationService().notify_actor_members(
            rfq["actor_id"],
            "proposal",
            "Новый отклик",
            f"Исполнитель отправил предложение на RFQ «{rfq['title']}»",
            f"/customer/rfqs/{rfq_id}/proposals",
        )
        return proposal_to_response(created)

    async def add_message(
        self,
        proposal_id: int,
        actor_id: int,
        user_id: int,
        data: ProposalMessageCreate,
        as_buyer: bool,
    ) -> dict:
        text = data.text.strip()
        if not text:
            raise ConflictError("Message cannot be empty")
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            assert_chat_access(row, actor_id, as_buyer)
            if row["status"] not in OPEN_CHAT_STATUSES:
                raise ConflictError("Chat is closed for this proposal")
            saved = await conn.fetchrow(
                """
                INSERT INTO proposal_messages (proposal_id, sender_id, text, created_at)
                VALUES ($1, $2, $3, NOW())
                RETURNING id
                """,
                proposal_id,
                user_id,
                text,
            )
            message = await conn.fetchrow(
                """
                SELECT
                    m.id, m.proposal_id, m.sender_id, m.text, m.created_at,
                    u.first_name, u.last_name
                FROM proposal_messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.id = $1
                """,
                saved["id"],
            )
        recipient_actor_id = row["supplier_actor_id"] if as_buyer else row["rfq_actor_id"]
        href = (
            f"/supplier/messages?proposal={proposal_id}"
            if as_buyer
            else f"/customer/messages?proposal={proposal_id}"
        )
        await NotificationService().notify_actor_members(
            recipient_actor_id,
            "proposal",
            "Новое сообщение по предложению",
            f"Обсуждение заявки «{row['rfq_title']}»",
            href,
        )
        return message_to_response(message)
