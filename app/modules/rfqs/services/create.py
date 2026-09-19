import uuid

from app.core.exceptions import ForbiddenError
from app.modules.rfqs.schemas import ProductRfqCreate, RfqCreate
from app.modules.rfqs.services.common import load_rfq_response, load_rfq_row


class RfqCreateMixin:
    async def create(self, actor_id: int, created_by: str, data: RfqCreate):
        rfq_id = str(uuid.uuid4())
        is_product = isinstance(data, ProductRfqCreate) or data.type == "product"
        async with self.connection() as conn:
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
                data.visibility,
                data.status or "draft",
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
            return await load_rfq_response(conn, rfq_id)

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
