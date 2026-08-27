from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.models import (
    ActorType,
    BudgetType,
    Company,
    Rfq,
    RfqAttachment,
    RfqInvitedSupplier,
    RfqStatus,
    RfqType,
    RfqVisibility,
)
from src.schemas.rfq import ProductRfqCreate, RfqCreate, ServiceRfqCreate
from src.services.serializers import rfq_to_response


RFQ_TRANSITIONS: dict[RfqStatus, set[RfqStatus]] = {
    RfqStatus.draft: {RfqStatus.published, RfqStatus.cancelled},
    RfqStatus.published: {RfqStatus.receiving_proposals, RfqStatus.cancelled, RfqStatus.expired},
    RfqStatus.receiving_proposals: {
        RfqStatus.supplier_selected,
        RfqStatus.cancelled,
        RfqStatus.expired,
    },
    RfqStatus.supplier_selected: {RfqStatus.contract_created, RfqStatus.cancelled},
    RfqStatus.contract_created: {RfqStatus.in_progress, RfqStatus.cancelled},
    RfqStatus.in_progress: {RfqStatus.completed, RfqStatus.disputed, RfqStatus.cancelled},
    RfqStatus.disputed: {RfqStatus.completed, RfqStatus.cancelled},
}

CLOSED_RFQ_STATUSES = [
    RfqStatus.supplier_selected,
    RfqStatus.contract_created,
    RfqStatus.in_progress,
    RfqStatus.completed,
    RfqStatus.cancelled,
    RfqStatus.expired,
    RfqStatus.disputed,
]

BUYER_TAB_STATUSES = {
    "draft": [RfqStatus.draft],
    "published": [RfqStatus.published],
    "collecting": [RfqStatus.receiving_proposals],
    "closed": CLOSED_RFQ_STATUSES,
    "active": [
        RfqStatus.published,
        RfqStatus.receiving_proposals,
        RfqStatus.in_progress,
        RfqStatus.disputed,
    ],
    "completed": [RfqStatus.completed, RfqStatus.cancelled, RfqStatus.expired],
}


class RfqService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load(self, rfq_id: str) -> Rfq:
        result = await self.db.execute(
            select(Rfq)
            .where(Rfq.id == rfq_id)
            .options(
                selectinload(Rfq.attachments),
                selectinload(Rfq.invited_suppliers),
            )
        )
        rfq = result.scalar_one_or_none()
        if not rfq:
            raise NotFoundError("RFQ not found")
        return rfq

    async def list_for_buyer(self, actor_id: int, tab: str | None = None) -> list:
        stmt = select(Rfq).where(Rfq.actor_id == actor_id).options(
            selectinload(Rfq.attachments), selectinload(Rfq.invited_suppliers)
        )
        if tab == "all" or not tab:
            pass
        elif tab in BUYER_TAB_STATUSES:
            stmt = stmt.where(Rfq.status.in_(BUYER_TAB_STATUSES[tab]))
        else:
            try:
                stmt = stmt.where(Rfq.status == RfqStatus(tab))
            except ValueError:
                pass
        result = await self.db.execute(stmt.order_by(Rfq.updated_at.desc()))
        return [rfq_to_response(r) for r in result.scalars().all()]

    async def list_board_for_supplier(self, supplier_id: int) -> list:
        result = await self.db.execute(
            select(Rfq)
            .where(
                Rfq.status.in_([RfqStatus.published, RfqStatus.receiving_proposals]),
            )
            .options(selectinload(Rfq.attachments), selectinload(Rfq.invited_suppliers))
        )
        rfqs = []
        for rfq in result.scalars().all():
            if rfq.visibility == RfqVisibility.public:
                rfqs.append(rfq)
            elif any(inv.supplier_id == supplier_id for inv in rfq.invited_suppliers):
                rfqs.append(rfq)
        return [rfq_to_response(r) for r in rfqs]

    async def get(self, rfq_id: str, actor_id: int | None = None, supplier_id: int | None = None):
        rfq = await self._load(rfq_id)
        if actor_id and rfq.actor_id != actor_id:
            if supplier_id:
                if rfq.visibility == RfqVisibility.invited_only:
                    if not any(i.supplier_id == supplier_id for i in rfq.invited_suppliers):
                        raise ForbiddenError("RFQ is invite-only")
            else:
                raise ForbiddenError("Access denied")
        return rfq_to_response(rfq)

    async def create(self, actor_id: int, created_by: str, data: RfqCreate):
        common = {
            "actor_id": actor_id,
            "created_by": created_by,
            "title": data.title,
            "description": data.description,
            "category_id": data.category_id,
            "budget_type": BudgetType(data.budget_type),
            "budget_from": data.budget_from,
            "budget_to": data.budget_to,
            "currency": data.currency,
            "deadline": data.deadline,
            "visibility": RfqVisibility(data.visibility),
            "status": RfqStatus(data.status or "draft"),
        }
        if isinstance(data, ProductRfqCreate) or data.type == "product":
            rfq = Rfq(
                **common,
                type=RfqType.product,
                quantity=data.quantity,
                delivery_country=data.delivery_country,
                delivery_city=data.delivery_city,
                delivery_address=data.delivery_address,
                delivery_date=data.delivery_date,
            )
        else:
            rfq = Rfq(
                **common,
                type=RfqType.service,
                project_duration=data.project_duration,
                start_date=data.start_date,
                team_size_required=data.team_size_required,
                experience_required=data.experience_required,
            )
        self.db.add(rfq)
        await self.db.flush()
        return rfq_to_response(await self._load(rfq.id))

    async def update(self, rfq_id: str, actor_id: int, data: dict):
        rfq = await self._load(rfq_id)
        if rfq.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        if rfq.status != RfqStatus.draft:
            raise ConflictError("Only draft RFQs can be edited")
        for key, val in data.items():
            if val is not None and hasattr(rfq, key) and key not in ("id", "actor_id", "type"):
                if key == "budget_type":
                    rfq.budget_type = BudgetType(val)
                elif key == "visibility":
                    rfq.visibility = RfqVisibility(val)
                else:
                    setattr(rfq, key, val)
        rfq.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return rfq_to_response(await self._load(rfq_id))

    async def _transition(self, rfq: Rfq, new_status: RfqStatus) -> None:
        allowed = RFQ_TRANSITIONS.get(rfq.status, set())
        if new_status not in allowed and new_status != RfqStatus.cancelled:
            if rfq.status == RfqStatus.draft and new_status == RfqStatus.published:
                pass
            elif rfq.status == RfqStatus.published and new_status == RfqStatus.receiving_proposals:
                pass
            else:
                raise ConflictError(f"Cannot transition from {rfq.status.value} to {new_status.value}")
        rfq.status = new_status
        rfq.updated_at = datetime.now(timezone.utc)

    async def publish(self, rfq_id: str, actor_id: int):
        rfq = await self._load(rfq_id)
        if rfq.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        if rfq.status != RfqStatus.draft:
            raise ConflictError("Only draft RFQs can be published")
        rfq.status = RfqStatus.published
        rfq.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        rfq.status = RfqStatus.receiving_proposals
        await self.db.flush()
        return rfq_to_response(await self._load(rfq_id))

    async def close(self, rfq_id: str, actor_id: int):
        rfq = await self._load(rfq_id)
        if rfq.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        rfq.status = RfqStatus.cancelled
        rfq.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return rfq_to_response(await self._load(rfq_id))

    async def add_attachment(self, rfq_id: str, actor_id: int, file_name: str, file_url: str, file_type: str):
        rfq = await self._load(rfq_id)
        if rfq.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        att = RfqAttachment(rfq_id=rfq_id, file_name=file_name, file_url=file_url, file_type=file_type)
        self.db.add(att)
        await self.db.flush()
        return rfq_to_response(await self._load(rfq_id))

    async def remove_attachment(self, rfq_id: str, actor_id: int, attachment_id: str):
        rfq = await self._load(rfq_id)
        if rfq.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        att = next((a for a in rfq.attachments if a.id == attachment_id), None)
        if not att:
            raise NotFoundError("Attachment not found")
        await self.db.delete(att)
        await self.db.flush()
        return rfq_to_response(await self._load(rfq_id))

    async def invite_suppliers(self, rfq_id: str, actor_id: int, supplier_ids: list[int]):
        rfq = await self._load(rfq_id)
        if rfq.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        invited_any = False
        for sid in supplier_ids:
            result = await self.db.execute(
                select(Company).where(Company.id == sid, Company.actor_type == ActorType.supplier)
            )
            if not result.scalar_one_or_none():
                continue
            existing = {i.supplier_id for i in rfq.invited_suppliers}
            if sid not in existing:
                invite = RfqInvitedSupplier(rfq_id=rfq_id, supplier_id=sid)
                self.db.add(invite)
                rfq.invited_suppliers.append(invite)
            invited_any = True
        if invited_any:
            rfq.visibility = RfqVisibility.invited_only
            rfq.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return rfq_to_response(rfq)
