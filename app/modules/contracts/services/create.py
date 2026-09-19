import json
from datetime import datetime, timezone

from app.core.exceptions import ConflictError, ForbiddenError
from app.modules.contracts.schemas import DisputeRequest, MessageCreate, WorkSubmissionCreate
from app.modules.contracts.services.common import (
    ACTIVE_DISPUTE_STATUSES,
    _custom_milestones,
    _default_milestones,
    ensure_not_under_dispute,
    ensure_party,
    enum_value,
    load_contract_row,
    load_contract_schema,
    message_to_dict,
    message_ws_payload,
    proposal_fields,
    resolve_actor_user_ids,
)
from app.utils.audit import AuditLogger
from app.enums import PaymentType
from app.modules.notifications.ws_manager import notification_ws_manager


class ContractCreateMixin:
    async def create_from_proposal(
        self,
        proposal,
        buyer_actor_id: int,
        payment_type: PaymentType | None = None,
        milestones: list[dict] | None = None,
        buyer_user_id: int | None = None,
    ) -> dict:
        data = proposal_fields(proposal)
        ptype = enum_value(payment_type) if payment_type else PaymentType.split_payment.value
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self.connection() as conn:
            contract = await conn.fetchrow(
                """
                INSERT INTO contracts (
                    rfq_id, proposal_id, buyer_actor_id, supplier_actor_id,
                    title, description, agreed_amount, currency, start_date, due_date,
                    payment_type, status, created_at
                )
                VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8::contract_currency, $9, $10,
                    $11::payment_type, 'pending_payment'::contract_status, NOW()
                )
                RETURNING id, agreed_amount
                """,
                data["rfq_id"],
                data["id"],
                buyer_actor_id,
                data["supplier_actor_id"],
                data["rfq_title"],
                data["rfq_description"],
                data["price"],
                data["currency"],
                start_date,
                data["rfq_deadline"],
                ptype,
            )
            plan = await conn.fetchrow(
                """
                INSERT INTO payment_plans (contract_id, payment_type)
                VALUES ($1, $2::plan_payment_type)
                RETURNING id
                """,
                contract["id"],
                ptype,
            )
            if ptype == PaymentType.milestone.value and milestones:
                plan_milestones = _custom_milestones(contract, milestones)
            else:
                plan_milestones = _default_milestones(contract, ptype)
            for item in plan_milestones:
                await conn.execute(
                    """
                    INSERT INTO payment_milestones (
                        payment_plan_id, contract_id, title, percentage, amount,
                        trigger, status
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7::milestone_status
                    )
                    """,
                    plan["id"],
                    contract["id"],
                    item["title"],
                    item["percentage"],
                    item["amount"],
                    item["trigger"],
                    enum_value(item["status"]),
                )
            await conn.execute(
                "INSERT INTO conversations (contract_id) VALUES ($1)",
                contract["id"],
            )
            await self._copy_rfq_attachments(
                conn,
                contract["id"],
                data["rfq_id"],
                buyer_actor_id,
                buyer_user_id,
            )
        return {"id": contract["id"]}

    async def _copy_rfq_attachments(
        self,
        conn,
        contract_id: int,
        rfq_id: str,
        buyer_actor_id: int,
        buyer_user_id: int | None,
    ) -> None:
        attachments = await conn.fetch(
            "SELECT file_name, file_url, file_type FROM rfq_attachments WHERE rfq_id = $1",
            rfq_id,
        )
        if not attachments:
            return
        uploaded_by = buyer_user_id
        if uploaded_by is None:
            user_ids = await resolve_actor_user_ids(conn, buyer_actor_id)
            uploaded_by = user_ids[0] if user_ids else None
        if uploaded_by is None:
            raise ConflictError("Cannot copy RFQ attachments: buyer user not found")
        for attachment in attachments:
            await conn.execute(
                """
                INSERT INTO contract_files (
                    contract_id, file_name, file_url, file_type, uploaded_by, created_at
                )
                VALUES ($1, $2, $3, $4, $5, NOW())
                """,
                contract_id,
                attachment["file_name"],
                attachment["file_url"],
                attachment["file_type"],
                uploaded_by,
            )

    async def _broadcast_message_event(
        self,
        conn,
        row,
        actor_id: int,
        event: str,
        payload_data: dict,
    ) -> None:
        recipient_actor_id = (
            row["supplier_actor_id"]
            if actor_id == row["buyer_actor_id"]
            else row["buyer_actor_id"]
        )
        recipient_user_ids = await resolve_actor_user_ids(conn, recipient_actor_id)
        sender_user_ids = await resolve_actor_user_ids(conn, actor_id)
        all_user_ids = list(set(recipient_user_ids + sender_user_ids))
        if not all_user_ids:
            return
        await notification_ws_manager.broadcast_to_users(
            all_user_ids,
            {"event": event, "data": payload_data},
        )

    async def add_message(self, contract_id: int, user_id: int, actor_id: int, data: MessageCreate):
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await ensure_party(row, actor_id)
            conversation_id = await conn.fetchval(
                "SELECT id FROM conversations WHERE contract_id = $1",
                contract_id,
            )
            if conversation_id is None:
                conversation_id = await conn.fetchval(
                    "INSERT INTO conversations (contract_id) VALUES ($1) RETURNING id",
                    contract_id,
                )
            message = await conn.fetchrow(
                """
                INSERT INTO messages (
                    conversation_id, sender_id, text, status, created_at
                )
                VALUES ($1, $2, $3, 'sent'::message_delivery_status, NOW())
                RETURNING id
                """,
                conversation_id,
                user_id,
                data.text,
            )
            recipient_actor_id = (
                row["supplier_actor_id"]
                if actor_id == row["buyer_actor_id"]
                else row["buyer_actor_id"]
            )
            recipient_user_ids = await resolve_actor_user_ids(conn, recipient_actor_id)
            if notification_ws_manager.is_any_online(recipient_user_ids):
                await conn.execute(
                    """
                    UPDATE messages
                    SET status = 'delivered'::message_delivery_status, delivered_at = NOW()
                    WHERE id = $1
                    """,
                    message["id"],
                )
            loaded = await conn.fetchrow(
                """
                SELECT
                    m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
                    m.status::text AS status, m.delivered_at, m.viewed_at,
                    u.first_name, u.last_name, u.email
                FROM messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.id = $1
                """,
                message["id"],
            )
            await self._broadcast_message_event(
                conn,
                row,
                actor_id,
                "contract.message",
                {
                    "contract_id": contract_id,
                    "message": message_ws_payload(loaded),
                },
            )
            return await load_contract_schema(conn, contract_id)

    async def add_file(
        self,
        contract_id: int,
        user_id: int,
        actor_id: int,
        file_name: str,
        file_url: str,
        file_type: str,
    ):
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await ensure_party(row, actor_id)
            await conn.execute(
                """
                INSERT INTO contract_files (
                    contract_id, file_name, file_url, file_type, uploaded_by, created_at
                )
                VALUES ($1, $2, $3, $4, $5, NOW())
                """,
                contract_id,
                file_name,
                file_url,
                file_type,
                user_id,
            )
            return await load_contract_schema(conn, contract_id)

    async def submit_work(
        self, contract_id: int, supplier_id: int, data: WorkSubmissionCreate
    ):
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            if row["supplier_actor_id"] != supplier_id:
                raise ForbiddenError("Access denied")
            await ensure_not_under_dispute(conn, row)
            if row["status"] not in ("active", "pending_payment"):
                raise ConflictError("Contract not active")
            assets = [
                {
                    "kind": asset.kind,
                    "name": asset.name,
                    "url": asset.url,
                    "file_type": asset.file_type,
                }
                for asset in data.assets
            ]
            file_names = data.file_names or [asset["name"] for asset in assets]
            await conn.execute(
                """
                INSERT INTO work_submissions (
                    contract_id, type, note, status, submitted_at, file_names, assets
                )
                VALUES (
                    $1, $2::submission_type, $3, 'pending'::submission_status,
                    NOW(), $4::text[], $5::jsonb
                )
                """,
                contract_id,
                data.type,
                data.note,
                file_names,
                json.dumps(assets),
            )
            await conn.execute(
                "UPDATE contracts SET status = 'delivered'::contract_status WHERE id = $1",
                contract_id,
            )
            return await load_contract_schema(conn, contract_id)

    async def open_dispute(self, contract_id: int, actor_id: int, data: DisputeRequest):
        async with self.connection() as conn:
            async with conn.transaction():
                row = await load_contract_row(conn, contract_id)
                await ensure_party(row, actor_id)
                if await conn.fetchval(
                    """
                    SELECT 1 FROM disputes
                    WHERE contract_id = $1 AND status::text = ANY($2::text[])
                    LIMIT 1
                    """,
                    contract_id,
                    list(ACTIVE_DISPUTE_STATUSES),
                ):
                    raise ConflictError("An active dispute already exists for this contract")
                had_resolved = await conn.fetchval(
                    """
                    SELECT 1 FROM disputes
                    WHERE contract_id = $1 AND status::text = 'resolved'
                    LIMIT 1
                    """,
                    contract_id,
                )
                status = "appealed" if had_resolved else "open"
                buyer_statement = data.reason if actor_id == row["buyer_actor_id"] else None
                supplier_statement = (
                    data.reason if actor_id == row["supplier_actor_id"] else None
                )
                dispute = await conn.fetchrow(
                    """
                    INSERT INTO disputes (
                        contract_id, status, opened_by_actor_id,
                        buyer_statement, supplier_statement, created_at, updated_at
                    )
                    VALUES (
                        $1, $2::dispute_status, $3, $4, $5, NOW(), NOW()
                    )
                    RETURNING id, status::text AS status
                    """,
                    contract_id,
                    status,
                    actor_id,
                    buyer_statement,
                    supplier_statement,
                )
                await conn.execute(
                    "UPDATE contracts SET status = 'disputed'::contract_status WHERE id = $1",
                    contract_id,
                )
                await conn.execute(
                    """
                    UPDATE rfqs SET status = 'disputed'::rfq_status
                    WHERE id = $1
                    """,
                    row["rfq_id"],
                )
                actor = await conn.fetchrow(
                    "SELECT user_id FROM actors WHERE id = $1",
                    actor_id,
                )
                await AuditLogger(conn).log_action(
                    user_id=actor["user_id"] if actor else None,
                    actor_id=actor_id,
                    company_id=None,
                    action="dispute.open",
                    resource_type="dispute",
                    resource_id=str(dispute["id"]),
                    details={
                        "contract_id": contract_id,
                        "status": dispute["status"],
                        "reason": data.reason,
                        "opened_by_actor_id": actor_id,
                    },
                )
            return await load_contract_schema(conn, contract_id)
