from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.rfqs.services.common import RFQ_UPDATABLE, load_rfq_response, load_rfq_row


class RfqUpdateMixin:
    async def update(self, rfq_id: str, actor_id: int, data: dict):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "draft":
                raise ConflictError("Only draft RFQs can be edited")
            sets = []
            params: list = []
            for key, val in data.items():
                if val is None or key not in RFQ_UPDATABLE:
                    continue
                params.append(val)
                if key == "budget_type":
                    sets.append(f"budget_type = ${len(params)}::budget_type")
                elif key == "visibility":
                    sets.append(f"visibility = ${len(params)}::rfq_visibility")
                elif key == "status":
                    sets.append(f"status = ${len(params)}::rfq_status")
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
            return await load_rfq_response(conn, rfq_id)

    async def publish(self, rfq_id: str, actor_id: int):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            if row["status"] != "draft":
                raise ConflictError("Only draft RFQs can be published")
            await conn.execute(
                """
                UPDATE rfqs
                SET status = 'receiving_proposals'::rfq_status, updated_at = NOW()
                WHERE id = $1
                """,
                rfq_id,
            )
            return await load_rfq_response(conn, rfq_id)

    async def close(self, rfq_id: str, actor_id: int):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                """
                UPDATE rfqs
                SET status = 'cancelled'::rfq_status, updated_at = NOW()
                WHERE id = $1
                """,
                rfq_id,
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
                invited_any = False
                for sid in supplier_ids:
                    actor = await conn.fetchrow(
                        """
                        SELECT id FROM actors
                        WHERE id = $1
                          AND kind::text = 'company'
                          AND side::text = 'supplier'
                          AND is_active = TRUE
                        """,
                        sid,
                    )
                    if not actor:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO rfq_invited_suppliers (rfq_id, supplier_actor_id)
                        VALUES ($1, $2)
                        ON CONFLICT (rfq_id, supplier_actor_id) DO NOTHING
                        """,
                        rfq_id,
                        sid,
                    )
                    invited_any = True
                if invited_any:
                    await conn.execute(
                        """
                        UPDATE rfqs
                        SET visibility = 'invited_only'::rfq_visibility, updated_at = NOW()
                        WHERE id = $1
                        """,
                        rfq_id,
                    )
                return await load_rfq_response(conn, rfq_id)
