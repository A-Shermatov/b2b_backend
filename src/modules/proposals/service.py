from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.models import (
    Currency,
    NotificationType,
    PaymentType,
    Proposal,
    ProposalMessage,
    ProposalStatus,
    Rfq,
    RfqStatus,
)
from src.modules.contracts.service import ContractService
from src.modules.notifications.service import NotificationService
from src.modules.proposals.schemas import ProposalCreate, ProposalMessageCreate, ProposalUpdate
from src.shared.serializers import proposal_to_schema


class ProposalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load(self, proposal_id: int) -> Proposal:
        result = await self.db.execute(
            select(Proposal)
            .where(Proposal.id == proposal_id)
            .options(
                selectinload(Proposal.attachment),
                selectinload(Proposal.rfq).selectinload(Rfq.attachments),
            )
        )
        p = result.scalar_one_or_none()
        if not p:
            raise NotFoundError("Proposal not found")
        return p

    async def list_for_rfq(self, rfq_id: str, buyer_actor_id: int) -> list:
        from src.modules.rfqs.service import RfqService

        rfq = await RfqService(self.db)._load(rfq_id)
        if rfq.actor_id != buyer_actor_id:
            raise ForbiddenError("Access denied")
        result = await self.db.execute(
            select(Proposal)
            .where(Proposal.rfq_id == rfq_id)
            .options(selectinload(Proposal.attachment))
            .order_by(Proposal.created_at.desc())
        )
        proposals = result.scalars().all()
        for p in proposals:
            if p.status == ProposalStatus.submitted:
                p.status = ProposalStatus.viewed
        await self.db.flush()
        return [proposal_to_schema(p) for p in proposals]

    async def list_for_supplier(self, supplier_id: int) -> list:
        result = await self.db.execute(
            select(Proposal)
            .where(Proposal.supplier_actor_id == supplier_id)
            .options(selectinload(Proposal.attachment))
            .order_by(Proposal.created_at.desc())
        )
        return [proposal_to_schema(p) for p in result.scalars().all()]

    async def submit(self, rfq_id: str, supplier_id: int, data: ProposalCreate):
        from src.modules.rfqs.service import RfqService

        rfq = await RfqService(self.db)._load(rfq_id)
        if rfq.status not in (RfqStatus.published, RfqStatus.receiving_proposals):
            raise ConflictError("RFQ is not accepting proposals")
        if rfq.visibility.value == "invited_only":
            if not any(i.supplier_actor_id == supplier_id for i in rfq.invited_suppliers):
                raise ForbiddenError("Not invited to this RFQ")
        existing = await self.db.execute(
            select(Proposal).where(
                Proposal.rfq_id == rfq_id, Proposal.supplier_actor_id == supplier_id
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError("Proposal already submitted")
        proposal = Proposal(
            rfq_id=rfq_id,
            supplier_actor_id=supplier_id,
            price=data.price,
            currency=Currency(data.currency),
            delivery_time=data.delivery_time,
            message=data.message,
            status=ProposalStatus.submitted,
        )
        self.db.add(proposal)
        await self.db.flush()
        notif = NotificationService(self.db)
        await notif.notify_actor_members(
            rfq.actor_id,
            NotificationType.proposal,
            "Новый отклик",
            f"Исполнитель отправил предложение на RFQ «{rfq.title}»",
            f"/customer/rfqs/{rfq.id}/proposals",
        )
        return proposal_to_schema(await self._load(proposal.id))

    async def update_proposal(self, proposal_id: int, supplier_id: int, data: ProposalUpdate):
        proposal = await self._load(proposal_id)
        if proposal.supplier_actor_id != supplier_id:
            raise ForbiddenError("Access denied")
        if proposal.status != ProposalStatus.submitted:
            raise ConflictError("Proposal cannot be edited")
        if data.price is not None:
            proposal.price = data.price
        if data.currency is not None:
            proposal.currency = Currency(data.currency)
        if data.delivery_time is not None:
            proposal.delivery_time = data.delivery_time
        if data.message is not None:
            proposal.message = data.message
        await self.db.flush()
        return proposal_to_schema(proposal)

    async def withdraw(self, proposal_id: int, supplier_id: int):
        proposal = await self._load(proposal_id)
        if proposal.supplier_actor_id != supplier_id:
            raise ForbiddenError("Access denied")
        proposal.status = ProposalStatus.withdrawn
        await self.db.flush()
        return proposal_to_schema(proposal)

    async def shortlist(self, proposal_id: int, buyer_actor_id: int):
        proposal = await self._load(proposal_id)
        if proposal.rfq.actor_id != buyer_actor_id:
            raise ForbiddenError("Access denied")
        proposal.status = ProposalStatus.shortlisted
        await self.db.flush()
        return proposal_to_schema(proposal)

    async def reject(self, proposal_id: int, buyer_actor_id: int):
        proposal = await self._load(proposal_id)
        if proposal.rfq.actor_id != buyer_actor_id:
            raise ForbiddenError("Access denied")
        proposal.status = ProposalStatus.rejected
        await self.db.flush()
        return proposal_to_schema(proposal)

    async def accept(
        self,
        proposal_id: int,
        buyer_actor_id: int,
        payment_type: PaymentType | None = None,
        milestones: list[dict] | None = None,
        buyer_user_id: int | None = None,
    ):
        proposal = await self._load(proposal_id)
        rfq = proposal.rfq
        if rfq.actor_id != buyer_actor_id:
            raise ForbiddenError("Access denied")
        if proposal.status in (ProposalStatus.accepted, ProposalStatus.rejected, ProposalStatus.withdrawn):
            raise ConflictError("Proposal already finalized")
        proposal.status = ProposalStatus.accepted
        rfq.status = RfqStatus.supplier_selected
        result = await self.db.execute(
            select(Proposal).where(Proposal.rfq_id == rfq.id, Proposal.id != proposal_id)
        )
        for other in result.scalars().all():
            if other.status not in (ProposalStatus.rejected, ProposalStatus.withdrawn):
                other.status = ProposalStatus.rejected
        await self.db.flush()
        contract_svc = ContractService(self.db)
        contract = await contract_svc.create_from_proposal(
            proposal,
            buyer_actor_id,
            payment_type=payment_type,
            milestones=milestones,
            buyer_user_id=buyer_user_id,
        )
        rfq.status = RfqStatus.contract_created
        await self.db.flush()
        notif = NotificationService(self.db)
        await notif.notify_supplier_on_accept(proposal, contract.id)
        return {"proposal": proposal_to_schema(proposal), "contract_id": contract.id}

    OPEN_CHAT_STATUSES = (
        ProposalStatus.submitted,
        ProposalStatus.viewed,
        ProposalStatus.shortlisted,
    )

    def _assert_chat_access(self, proposal: Proposal, actor_id: int, as_buyer: bool) -> None:
        if as_buyer:
            if proposal.rfq.actor_id != actor_id:
                raise ForbiddenError("Access denied")
            return
        if proposal.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")

    def _message_to_schema(self, message: ProposalMessage) -> dict:
        sender = message.sender
        sender_name = ""
        if sender:
            sender_name = f"{sender.first_name} {sender.last_name}".strip()
        return {
            "id": message.id,
            "proposal_id": message.proposal_id,
            "sender_id": message.sender_id,
            "sender_name": sender_name,
            "text": message.text,
            "created_at": message.created_at,
        }

    async def list_messages(self, proposal_id: int, actor_id: int, as_buyer: bool) -> list:
        proposal = await self._load(proposal_id)
        self._assert_chat_access(proposal, actor_id, as_buyer)
        result = await self.db.execute(
            select(ProposalMessage)
            .where(ProposalMessage.proposal_id == proposal_id)
            .options(selectinload(ProposalMessage.sender))
            .order_by(ProposalMessage.created_at.asc())
        )
        return [self._message_to_schema(m) for m in result.scalars().all()]

    async def add_message(
        self,
        proposal_id: int,
        actor_id: int,
        user_id: int,
        data: ProposalMessageCreate,
        as_buyer: bool,
    ) -> dict:
        proposal = await self._load(proposal_id)
        self._assert_chat_access(proposal, actor_id, as_buyer)
        if proposal.status not in self.OPEN_CHAT_STATUSES:
            raise ConflictError("Chat is closed for this proposal")
        text = data.text.strip()
        if not text:
            raise ConflictError("Message cannot be empty")
        message = ProposalMessage(
            proposal_id=proposal.id,
            sender_id=user_id,
            text=text,
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)
        result = await self.db.execute(
            select(ProposalMessage)
            .where(ProposalMessage.id == message.id)
            .options(selectinload(ProposalMessage.sender))
        )
        saved = result.scalar_one()

        rfq = proposal.rfq
        recipient_actor_id = (
            proposal.supplier_actor_id if as_buyer else rfq.actor_id
        )
        href = (
            f"/supplier/rfqs/{rfq.id}"
            if as_buyer
            else f"/customer/rfqs/{rfq.id}/proposals"
        )
        await NotificationService(self.db).notify_actor_members(
            recipient_actor_id,
            NotificationType.proposal,
            "Новое сообщение по предложению",
            f"Обсуждение заявки «{rfq.title}»",
            href,
        )
        return self._message_to_schema(saved)
