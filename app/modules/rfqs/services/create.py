import uuid

from app.core.exceptions import ForbiddenError, ValidationError
from app.modules.rfqs.schemas import ProductRfqCreate, RfqCreate
from app.modules.rfqs.services.common import (
    OPEN_RFQ_STATUSES,
    insert_invites,
    load_rfq_response,
    load_rfq_row,
    notify_invited_suppliers,
    validate_supplier_actor_ids,
)
from app.utils.audit import log_event


from app.modules.rfqs.services.subscriptions import notify_subscribers_safely


class RfqCreateMixin:
    async def create(self, actor_id: int, created_by: str, data: RfqCreate):
        rfq_id = str(uuid.uuid4())
        is_product = isinstance(data, ProductRfqCreate) or data.type == "product"
        status = data.status or "draft"
        invited: list[int] = []
        async with self.connection() as conn:
            async with conn.transaction():
                wants_invites = data.visibility == "invited_only" or bool(data.invited_supplier_ids)
                if wants_invites:
                    invited = await validate_supplier_actor_ids(
                        conn,
                        data.invited_supplier_ids,
                        exclude_actor_id=actor_id,
                    )
                    if not invited:
                        raise ValidationError("Исполнитель для персональной заявки не найден")
                visibility = "invited_only" if invited else data.visibility
                await conn.execute(
                    """
                    INSERT INTO rfqs (
                        id, actor_id, created_by, type, title, description, category_id,
                        budget_type, budget_from, budget_to, currency, deadline,
                        visibility, status,
                        quantity, delivery_country, delivery_city, delivery_address, delivery_date,
                        project_duration, start_date, team_size_required, experience_required,
                        created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4::rfq_type, $5, $6, $7,
                        $8::budget_type, $9, $10, $11, $12,
                        $13::rfq_visibility, $14::rfq_status,
                        $15, $16, $17, $18, $19,
                        $20, $21, $22, $23,
                        NOW(), NOW()
                    )
                    """,
                    rfq_id,
                    actor_id,
                    created_by,
                    "product" if is_product else "service",
                    data.title,
                    data.description,
                    data.category_id,
                    data.budget_type,
                    data.budget_from,
                    data.budget_to,
                    data.currency,
                    data.deadline,
                    visibility,
                    status,
                    data.quantity if is_product else None,
                    data.delivery_country if is_product else None,
                    data.delivery_city if is_product else None,
                    data.delivery_address if is_product else None,
                    data.delivery_date if is_product else None,
                    None if is_product else data.project_duration,
                    None if is_product else data.start_date,
                    None if is_product else data.team_size_required,
                    None if is_product else data.experience_required,
                )
                await insert_invites(conn, rfq_id, invited)
                await log_event(
                    conn,
                    "rfq.create",
                    "rfq",
                    rfq_id,
                    actor_id=actor_id,
                    details={
                        "title": data.title,
                        "type": "product" if is_product else "service",
                        "status": status,
                        "visibility": visibility,
                        "invited_count": len(invited),
                    },
                )
            response = await load_rfq_response(conn, rfq_id)
        if invited and status in OPEN_RFQ_STATUSES:
            await notify_invited_suppliers(rfq_id, data.title, invited)
        if status in OPEN_RFQ_STATUSES and visibility == "public":
            await notify_subscribers_safely(rfq_id, data.title, data.category_id, actor_id, invited)
        return response

    async def add_attachment(
        self,
        rfq_id: str,
        actor_id: int,
        file_name: str,
        file_url: str,
        file_type: str,
    ):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                """
                INSERT INTO rfq_attachments (id, rfq_id, file_name, file_url, file_type)
                VALUES ($1, $2, $3, $4, $5)
                """,
                str(uuid.uuid4()),
                rfq_id,
                file_name,
                file_url,
                file_type,
            )
            return await load_rfq_response(conn, rfq_id)
