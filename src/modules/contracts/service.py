from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.models import (
    Actor,
    ActorKind,
    Contract,
    ContractFile,
    ContractStatus,
    Conversation,
    Currency,
    Dispute,
    DisputeStatus,
    Message,
    MessageDeliveryStatus,
    PaymentMilestone,
    PaymentMilestoneStatus,
    PaymentMilestoneTrigger,
    PaymentPlan,
    PaymentType,
    Proposal,
    Rfq,
    RfqAttachment,
    RfqStatus,
    WorkSubmission,
    WorkSubmissionStatus,
    WorkSubmissionType,
)
from src.modules.contracts.schemas import DisputeRequest, MessageCreate, WorkSubmissionCreate
from src.modules.notifications.ws_manager import notification_ws_manager
from src.shared.serializers import contract_to_schema
from src.utils.audit import log_audit


def _default_milestones(contract: Contract, payment_type: PaymentType) -> list[dict]:
    amount = contract.agreed_amount
    if payment_type == PaymentType.full_prepayment:
        return [
            {
                "title": "Полная предоплата",
                "percentage": 100,
                "amount": amount,
                "trigger": PaymentMilestoneTrigger.contract_signed.value,
                "status": PaymentMilestoneStatus.awaiting_payment,
            }
        ]
    if payment_type == PaymentType.full_postpayment:
        return [
            {
                "title": "Оплата после приёмки",
                "percentage": 100,
                "amount": amount,
                "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
                "status": PaymentMilestoneStatus.pending,
            }
        ]
    if payment_type == PaymentType.split_payment:
        return [
            {
                "title": "Предоплата 50%",
                "percentage": 50,
                "amount": round(amount * 0.5, 2),
                "trigger": PaymentMilestoneTrigger.contract_signed.value,
                "status": PaymentMilestoneStatus.awaiting_payment,
            },
            {
                "title": "Оплата 50% после приёмки",
                "percentage": 50,
                "amount": round(amount * 0.5, 2),
                "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
                "status": PaymentMilestoneStatus.pending,
            },
        ]
    return [
        {
            "title": "Этап 1 — подписание",
            "percentage": 30,
            "amount": round(amount * 0.3, 2),
            "trigger": PaymentMilestoneTrigger.contract_signed.value,
            "status": PaymentMilestoneStatus.awaiting_payment,
        },
        {
            "title": "Этап 2 — промежуточная сдача",
            "percentage": 40,
            "amount": round(amount * 0.4, 2),
            "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
            "status": PaymentMilestoneStatus.pending,
        },
        {
            "title": "Этап 3 — финальная приёмка",
            "percentage": 30,
            "amount": round(amount * 0.3, 2),
            "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
            "status": PaymentMilestoneStatus.pending,
        },
    ]


def _custom_milestones(contract: Contract, milestones: list[dict]) -> list[dict]:
    amount = contract.agreed_amount
    result: list[dict] = []
    allocated = 0.0
    last_index = len(milestones) - 1
    for index, m in enumerate(milestones):
        trigger = m["trigger"]
        trigger_value = trigger.value if hasattr(trigger, "value") else trigger
        if index == last_index:
            stage_amount = round(amount - allocated, 2)
        else:
            stage_amount = round(m["percentage"] / 100 * amount, 2)
            allocated += stage_amount
        status = (
            PaymentMilestoneStatus.awaiting_payment
            if trigger_value == PaymentMilestoneTrigger.contract_signed.value
            else PaymentMilestoneStatus.pending
        )
        result.append(
            {
                "title": m["title"],
                "percentage": m["percentage"],
                "amount": stage_amount,
                "trigger": trigger_value,
                "status": status,
            }
        )
    return result


class ContractService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load(self, contract_id: int) -> Contract:
        result = await self.db.execute(
            select(Contract)
            .where(Contract.id == contract_id)
            .options(
                selectinload(Contract.payment_plan).selectinload(PaymentPlan.milestones),
                selectinload(Contract.conversation)
                .selectinload(Conversation.messages)
                .selectinload(Message.sender),
                selectinload(Contract.files),
                selectinload(Contract.submissions),
                selectinload(Contract.rfq),
            )
        )
        c = result.scalar_one_or_none()
        if not c:
            raise NotFoundError("Contract not found")
        return c

    async def create_from_proposal(
        self,
        proposal: Proposal,
        buyer_actor_id: int,
        payment_type: PaymentType | None = None,
        milestones: list[dict] | None = None,
        buyer_user_id: int | None = None,
    ) -> Contract:
        rfq = proposal.rfq
        if payment_type is None:
            payment_type = PaymentType.split_payment
        contract = Contract(
            rfq_id=rfq.id,
            proposal_id=proposal.id,
            buyer_actor_id=buyer_actor_id,
            supplier_actor_id=proposal.supplier_actor_id,
            title=rfq.title,
            description=rfq.description,
            agreed_amount=proposal.price,
            currency=proposal.currency,
            start_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            due_date=rfq.deadline,
            payment_type=payment_type,
            status=ContractStatus.pending_payment,
        )
        self.db.add(contract)
        await self.db.flush()
        plan = PaymentPlan(contract_id=contract.id, payment_type=payment_type)
        self.db.add(plan)
        await self.db.flush()
        if payment_type == PaymentType.milestone and milestones:
            plan_milestones = _custom_milestones(contract, milestones)
        else:
            plan_milestones = _default_milestones(contract, payment_type)
        for m in plan_milestones:
            self.db.add(
                PaymentMilestone(
                    payment_plan_id=plan.id,
                    contract_id=contract.id,
                    title=m["title"],
                    percentage=m["percentage"],
                    amount=m["amount"],
                    trigger=m["trigger"],
                    status=m["status"],
                )
            )
        conv = Conversation(contract_id=contract.id)
        self.db.add(conv)
        await self.db.flush()
        await self._copy_rfq_attachments(contract, rfq, buyer_actor_id, buyer_user_id)
        return contract

    async def _copy_rfq_attachments(
        self,
        contract: Contract,
        rfq: Rfq,
        buyer_actor_id: int,
        buyer_user_id: int | None,
    ) -> None:
        result = await self.db.execute(
            select(RfqAttachment).where(RfqAttachment.rfq_id == rfq.id)
        )
        attachments = list(result.scalars().all())
        if not attachments:
            return
        uploaded_by = buyer_user_id
        if uploaded_by is None:
            user_ids = await self._resolve_actor_user_ids(buyer_actor_id)
            uploaded_by = user_ids[0] if user_ids else None
        if uploaded_by is None:
            raise ConflictError("Cannot copy RFQ attachments: buyer user not found")
        for attachment in attachments:
            self.db.add(
                ContractFile(
                    contract_id=contract.id,
                    file_name=attachment.file_name,
                    file_url=attachment.file_url,
                    file_type=attachment.file_type,
                    uploaded_by=uploaded_by,
                )
            )
        await self.db.flush()

    async def list_for_actor(self, actor_id: int, actor_type: str) -> list:
        if actor_type == "buyer":
            stmt = select(Contract).where(Contract.buyer_actor_id == actor_id)
        else:
            stmt = select(Contract).where(Contract.supplier_actor_id == actor_id)
        result = await self.db.execute(
            stmt.options(
                selectinload(Contract.payment_plan).selectinload(PaymentPlan.milestones),
                selectinload(Contract.conversation)
                .selectinload(Conversation.messages)
                .selectinload(Message.sender),
                selectinload(Contract.files),
                selectinload(Contract.submissions),
            ).order_by(Contract.created_at.desc())
        )
        return [contract_to_schema(c) for c in result.scalars().all()]

    async def get(self, contract_id: int, actor_id: int) -> dict:
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != actor_id and contract.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")
        return contract_to_schema(contract)

    async def _resolve_actor_user_ids(self, actor_id: int) -> list[int]:
        actor = (
            await self.db.execute(select(Actor).where(Actor.id == actor_id))
        ).scalar_one_or_none()
        if not actor:
            return []
        if actor.kind == ActorKind.individual:
            return [actor.user_id] if actor.user_id else []
        if actor.company_id:
            from src.models import CompanyUser
            members = await self.db.execute(
                select(CompanyUser.user_id).where(CompanyUser.company_id == actor.company_id)
            )
            return [row[0] for row in members.all()]
        return []

    def _message_payload(self, contract_id: int, msg: Message, sender_name: str | None = None) -> dict:
        name = sender_name
        if name is None:
            sender = getattr(msg, "sender", None)
            if sender:
                name = f"{sender.first_name} {sender.last_name}".strip() or sender.email
            else:
                name = f"User #{msg.sender_id}"
        return {
            "id": msg.id,
            "conversation_id": msg.conversation_id,
            "sender_id": msg.sender_id,
            "sender_name": name or f"User #{msg.sender_id}",
            "text": msg.text,
            "attachment": None,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "status": msg.status.value if hasattr(msg.status, "value") else (msg.status or "sent"),
            "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
            "viewed_at": msg.viewed_at.isoformat() if msg.viewed_at else None,
        }

    async def _broadcast_message_event(
        self,
        contract: Contract,
        actor_id: int,
        event: str,
        payload_data: dict,
    ) -> None:
        recipient_actor_id = (
            contract.supplier_actor_id
            if actor_id == contract.buyer_actor_id
            else contract.buyer_actor_id
        )
        recipient_user_ids = await self._resolve_actor_user_ids(recipient_actor_id)
        sender_user_ids = await self._resolve_actor_user_ids(actor_id)
        all_user_ids = list(set(recipient_user_ids + sender_user_ids))
        if not all_user_ids:
            return
        await notification_ws_manager.broadcast_to_users(
            all_user_ids,
            {"event": event, "data": payload_data},
        )

    async def add_message(self, contract_id: int, user_id: int, actor_id: int, data: MessageCreate):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != actor_id and contract.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")
        if not contract.conversation:
            contract.conversation = Conversation(contract_id=contract_id)
            await self.db.flush()

        now = datetime.now(timezone.utc)
        msg = Message(
            conversation_id=contract.conversation.id,
            sender_id=user_id,
            text=data.text,
            status=MessageDeliveryStatus.sent,
        )
        self.db.add(msg)
        await self.db.flush()
        await self.db.refresh(msg, attribute_names=["sender"])

        sender_name = ""
        if msg.sender:
            sender_name = f"{msg.sender.first_name} {msg.sender.last_name}".strip()
            if not sender_name:
                sender_name = msg.sender.email or ""

        recipient_actor_id = (
            contract.supplier_actor_id
            if actor_id == contract.buyer_actor_id
            else contract.buyer_actor_id
        )
        recipient_user_ids = await self._resolve_actor_user_ids(recipient_actor_id)
        if notification_ws_manager.is_any_online(recipient_user_ids):
            msg.status = MessageDeliveryStatus.delivered
            msg.delivered_at = now
            await self.db.flush()

        await self._broadcast_message_event(
            contract,
            actor_id,
            "contract.message",
            {
                "contract_id": contract_id,
                "message": self._message_payload(contract_id, msg, sender_name),
            },
        )

        return contract_to_schema(await self._load(contract_id))
        if notification_ws_manager.is_any_online(recipient_user_ids):
            msg.status = MessageDeliveryStatus.delivered
            msg.delivered_at = now
            await self.db.flush()

        await self._broadcast_message_event(
            contract,
            actor_id,
            "contract.message",
            {
                "contract_id": contract_id,
                "message": self._message_payload(contract_id, msg, sender_name),
            },
        )

        if msg not in contract.conversation.messages:
            contract.conversation.messages.append(msg)
        self.db.expire(contract.conversation, ["messages"])
        return contract_to_schema(await self._load(contract_id))

    async def mark_message_delivered(
        self, contract_id: int, message_id: int, user_id: int, actor_id: int
    ):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != actor_id and contract.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")
        if not contract.conversation:
            raise NotFoundError("Conversation not found")
        msg = next(
            (m for m in contract.conversation.messages if m.id == message_id),
            None,
        )
        if not msg:
            raise NotFoundError("Message not found")
        if msg.sender_id == user_id:
            raise ForbiddenError("Cannot acknowledge own message")
        if msg.status == MessageDeliveryStatus.sent:
            msg.status = MessageDeliveryStatus.delivered
            msg.delivered_at = datetime.now(timezone.utc)
            await self.db.flush()
            await self._broadcast_message_event(
                contract,
                actor_id,
                "contract.message.status",
                {
                    "contract_id": contract_id,
                    "message": self._message_payload(contract_id, msg),
                },
            )
        return self._message_payload(contract_id, msg)

    async def mark_messages_read(self, contract_id: int, user_id: int, actor_id: int):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != actor_id and contract.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")
        if not contract.conversation:
            return {"message_ids": []}

        now = datetime.now(timezone.utc)
        updated: list[Message] = []
        for msg in contract.conversation.messages:
            if msg.sender_id == user_id:
                continue
            if msg.status == MessageDeliveryStatus.viewed:
                continue
            if msg.status == MessageDeliveryStatus.sent:
                msg.delivered_at = msg.delivered_at or now
            msg.status = MessageDeliveryStatus.viewed
            msg.viewed_at = now
            updated.append(msg)

        if not updated:
            return {"message_ids": []}

        await self.db.flush()
        payloads = [self._message_payload(contract_id, msg) for msg in updated]
        await self._broadcast_message_event(
            contract,
            actor_id,
            "contract.message.status",
            {
                "contract_id": contract_id,
                "messages": payloads,
                "message": payloads[0],
            },
        )
        return {"message_ids": [m.id for m in updated], "messages": payloads}

    async def add_file(
        self, contract_id: int, user_id: int, actor_id: int, file_name: str, file_url: str, file_type: str
    ):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != actor_id and contract.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")
        self.db.add(
            ContractFile(
                contract_id=contract_id,
                file_name=file_name,
                file_url=file_url,
                file_type=file_type,
                uploaded_by=user_id,
            )
        )
        await self.db.flush()
        return contract_to_schema(await self._load(contract_id))

    async def submit_work(
        self, contract_id: int, supplier_id: int, data: WorkSubmissionCreate
    ):
        contract = await self._load(contract_id)
        if contract.supplier_actor_id != supplier_id:
            raise ForbiddenError("Access denied")
        if contract.status not in (ContractStatus.active, ContractStatus.pending_payment):
            raise ConflictError("Contract not active")
        assets = [
            {
                "kind": a.kind,
                "name": a.name,
                "url": a.url,
                "file_type": a.file_type,
            }
            for a in data.assets
        ]
        file_names = data.file_names or [a["name"] for a in assets]
        sub = WorkSubmission(
            contract_id=contract_id,
            type=WorkSubmissionType(data.type),
            note=data.note,
            status=WorkSubmissionStatus.pending,
            file_names=file_names,
            assets=assets,
        )
        self.db.add(sub)
        contract.status = ContractStatus.delivered
        await self.db.flush()
        return contract_to_schema(await self._load(contract_id))

    async def approve_submission(self, contract_id: int, submission_id: int, buyer_id: int):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != buyer_id:
            raise ForbiddenError("Access denied")
        sub = next((s for s in contract.submissions if s.id == submission_id), None)
        if not sub:
            raise NotFoundError("Submission not found")
        sub.status = WorkSubmissionStatus.accepted
        contract.status = ContractStatus.completed
        if contract.rfq:
            contract.rfq.status = RfqStatus.completed
        if contract.payment_plan:
            for m in contract.payment_plan.milestones:
                if m.status == PaymentMilestoneStatus.funded:
                    m.status = PaymentMilestoneStatus.released
                elif m.status == PaymentMilestoneStatus.pending:
                    m.status = PaymentMilestoneStatus.awaiting_payment
        await self.db.flush()
        return contract_to_schema(await self._load(contract_id))

    async def reject_submission(self, contract_id: int, submission_id: int, buyer_id: int):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != buyer_id:
            raise ForbiddenError("Access denied")
        sub = next((s for s in contract.submissions if s.id == submission_id), None)
        if not sub:
            raise NotFoundError("Submission not found")
        sub.status = WorkSubmissionStatus.rejected
        contract.status = ContractStatus.active
        await self.db.flush()
        return contract_to_schema(await self._load(contract_id))

    async def open_dispute(self, contract_id: int, actor_id: int, data: DisputeRequest):
        contract = await self._load(contract_id)
        if contract.buyer_actor_id != actor_id and contract.supplier_actor_id != actor_id:
            raise ForbiddenError("Access denied")

        active = await self.db.execute(
            select(Dispute).where(
                Dispute.contract_id == contract_id,
                Dispute.status.in_(
                    [
                        DisputeStatus.open,
                        DisputeStatus.under_review,
                        DisputeStatus.appealed,
                    ]
                ),
            )
        )
        if active.scalar_one_or_none():
            raise ConflictError("An active dispute already exists for this contract")

        had_resolved = (
            await self.db.execute(
                select(Dispute.id)
                .where(
                    Dispute.contract_id == contract_id,
                    Dispute.status == DisputeStatus.resolved,
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        buyer_statement = data.reason if actor_id == contract.buyer_actor_id else None
        supplier_statement = (
            data.reason if actor_id == contract.supplier_actor_id else None
        )
        dispute = Dispute(
            contract_id=contract.id,
            status=DisputeStatus.appealed if had_resolved else DisputeStatus.open,
            opened_by_actor_id=actor_id,
            buyer_statement=buyer_statement,
            supplier_statement=supplier_statement,
        )
        self.db.add(dispute)
        contract.status = ContractStatus.disputed
        if contract.rfq:
            contract.rfq.status = RfqStatus.disputed
        await self.db.flush()

        actor = (
            await self.db.execute(select(Actor).where(Actor.id == actor_id))
        ).scalar_one_or_none()
        await log_audit(
            self.db,
            user_id=actor.user_id if actor else None,
            action="dispute.open",
            resource_type="dispute",
            resource_id=str(dispute.id),
            details={
                "contract_id": contract.id,
                "status": dispute.status.value,
                "reason": data.reason,
                "opened_by_actor_id": actor_id,
            },
        )
        await self.db.flush()
        return contract_to_schema(await self._load(contract_id))
