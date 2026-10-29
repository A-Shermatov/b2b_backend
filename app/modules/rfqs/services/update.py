from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.modules.rfqs.schemas import RfqUpdate
from app.modules.rfqs.services.common import (
    OPEN_RFQ_STATUSES,
    RFQ_UPDATABLE_COMMON,
    RFQ_UPDATABLE_PRODUCT,
    RFQ_UPDATABLE_SERVICE,
    fetch_invites_map,
    insert_invites,
    load_rfq_response,
    load_rfq_row,
    notify_invited_suppliers,
    validate_supplier_actor_ids,
)
from app.utils.audit import log_event


class RfqUpdateMixin:
    async def update(self, rfq_id: str, actor_id: int, data: RfqUpdate):
        async with self.connection() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT id FROM rfqs WHERE id = $1 FOR UPDATE", rfq_id
                )
                if not row:
                    raise NotFoundError("RFQ not found")
                row = await load_rfq_row(conn, rfq_id)
                if row["actor_id"] != actor_id:
                    raise ForbiddenError("Access denied")
                if row["status"] not in ("draft", *OPEN_RFQ_STATUSES):
                    raise ConflictError("RFQ cannot be edited in its current status")
                if await conn.fetchval(
                    "SELECT 1 FROM contracts WHERE rfq_id = $1 LIMIT 1", rfq_id
                ):
                    raise ConflictError("RFQ already has a contract")
                allowed = set(RFQ_UPDATABLE_COMMON)
                allowed |= (
                    RFQ_UPDATABLE_PRODUCT if row["type"] == "product" else RFQ_UPDATABLE_SERVICE
                )
                if row["status"] == "draft":
                    allowed.add("visibility")
                elif data.visibility is not None and data.visibility != row["visibility"]:
                    raise ConflictError("Visibility can only be changed for draft RFQs")
                sets = []
                params: list = []
                before: dict = {}
                after: dict = {}
                for key, val in data.model_dump(exclude_none=True).items():
                    if key not in allowed:
                        continue
                    if key in row.keys() and row[key] != val:
                        before[key] = row[key]
                        after[key] = val
                    params.append(val)
                    if key == "budget_type":
                        sets.append(f"budget_type = ${len(params)}::budget_type")
                    elif key == "visibility":
                        sets.append(f"visibility = ${len(params)}::rfq_visibility")
                    else:
                        sets.append(f"{key} = ${len(params)}")
                if sets:
                    params.append(rfq_id)
                    await conn.execute(
                        f"""
                        UPDATE rfqs
                        SET {', '.join(sets)}, updated_at = NOW()
                        WHERE id = ${len(params)}
                        """,
                        *params,
                    )
                else:
                    await conn.execute(
                        "UPDATE rfqs SET updated_at = NOW() WHERE id = $1",
                        rfq_id,
                    )
                await log_event(
                    conn,
                    "rfq.update",
                    "rfq",
                    rfq_id,
                    actor_id=actor_id,
                    details={"before": before, "after": after},
                )
            return await load_rfq_response(conn, rfq_id)

    async def publish(self, rfq_id: str, actor_id: int):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "draft":
                raise ConflictError("Only draft RFQs can be published")
            invited = (await fetch_invites_map(conn, [rfq_id]))[rfq_id]
            if row["visibility"] == "invited_only" and not invited:
                raise ValidationError("Исполнитель для персональной заявки не найден")
            await conn.execute(
                """
                UPDATE rfqs
                SET status = 'receiving_proposals'::rfq_status, updated_at = NOW()
                WHERE id = $1
                """,
                rfq_id,
            )
            await log_event(
                conn,
                "rfq.publish",
                "rfq",
                rfq_id,
                actor_id=actor_id,
                details={"before": {"status": "draft"}, "after": {"status": "receiving_proposals"}},
            )
            response = await load_rfq_response(conn, rfq_id)
        if invited:
            await notify_invited_suppliers(rfq_id, row["title"], invited)
        return response

    async def close(self, rfq_id: str, actor_id: int):
        async with self.connection() as conn:
            async with conn.transaction():
                await conn.fetchval("SELECT id FROM rfqs WHERE id = $1 FOR UPDATE", rfq_id)
                row = await load_rfq_row(conn, rfq_id)
                if row["actor_id"] != actor_id:
                    raise ForbiddenError("Access denied")
                if row["status"] not in ("draft", *OPEN_RFQ_STATUSES):
                    raise ConflictError("RFQ cannot be closed in its current status")
                await conn.execute(
                    """
                    UPDATE rfqs
                    SET status = 'cancelled'::rfq_status, updated_at = NOW()
                    WHERE id = $1
                    """,
                    rfq_id,
                )
                rejected = await conn.execute(
                    """
                    UPDATE proposals
                    SET status = 'rejected'::proposal_status
                    WHERE rfq_id = $1
                      AND status::text IN ('submitted', 'viewed', 'shortlisted', 'pending_supplier')
                    """,
                    rfq_id,
                )
                await log_event(
                    conn,
                    "rfq.close",
                    "rfq",
                    rfq_id,
                    actor_id=actor_id,
                    details={
                        "before": {"status": row["status"]},
                        "after": {"status": "cancelled"},
                        "rejected_proposals": int(rejected.split()[-1]),
                    },
                )
            return await load_rfq_response(conn, rfq_id)

    async def remove_attachment(self, rfq_id: str, actor_id: int, attachment_id: str):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            deleted = await conn.fetchval(
                """
                DELETE FROM rfq_attachments
                WHERE id = $1 AND rfq_id = $2
                RETURNING id
                """,
                attachment_id,
                rfq_id,
            )
            if not deleted:
                raise NotFoundError("Attachment not found")
            return await load_rfq_response(conn, rfq_id)

    async def invite_suppliers(self, rfq_id: str, actor_id: int, supplier_ids: list[int]):
        async with self.connection() as conn:
            async with conn.transaction():
                row = await load_rfq_row(conn, rfq_id)
                if row["actor_id"] != actor_id:
                    raise ForbiddenError("Access denied")
                if row["status"] not in ("draft", *OPEN_RFQ_STATUSES):
                    raise ConflictError("RFQ is closed for invitations")
                valid_ids = await validate_supplier_actor_ids(
                    conn,
                    supplier_ids,
                    exclude_actor_id=actor_id,
                )
                if not valid_ids:
                    raise ValidationError("Исполнитель для персональной заявки не найден")
                newly_invited = await insert_invites(conn, rfq_id, valid_ids)
                await log_event(
                    conn,
                    "rfq.invite",
                    "rfq",
                    rfq_id,
                    actor_id=actor_id,
                    details={"supplier_actor_ids": list(newly_invited or [])},
                )
                await conn.execute(
                    """
                    UPDATE rfqs
                    SET visibility = 'invited_only'::rfq_visibility, updated_at = NOW()
                    WHERE id = $1
                    """,
                    rfq_id,
                )
            response = await load_rfq_response(conn, rfq_id)
        if newly_invited and row["status"] in OPEN_RFQ_STATUSES:
            await notify_invited_suppliers(rfq_id, row["title"], newly_invited)
        return response
