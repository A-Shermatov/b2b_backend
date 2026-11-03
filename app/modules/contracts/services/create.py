import json
from datetime import datetime, timezone

from app.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError
from app.modules.contracts.schemas import (
    DisputeRequest,
    DisputeStatementRequest,
    MessageCreate,
    WorkSubmissionCreate,
)
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
    resolve_contract_due_date,
)
from app.modules.finance.services.commission import calculate_commission, split_commission
from app.modules.settings.service import get_platform_settings
from app.utils.audit import AuditLogger, log_event
from app.utils.storage import storage_key_from_url
from app.enums import PaymentType
from app.modules.notifications.services import NotificationService
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
        ptype = enum_value(payment_type) if payment_type else PaymentType.full_postpayment.value
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        due_date = resolve_contract_due_date(
            start_date,
            data.get("delivery_time"),
            data.get("rfq_deadline"),
        )
        async with self.connection() as conn:
            settings = await get_platform_settings(conn, use_cache=False)
            # Alif Marketplace splits each payment into supplier + commission parts.
            bank_split = get_settings().alif_marketplace_enabled
            quote = calculate_commission(
                float(data["price"]), data["currency"], settings, bank_split=bank_split
            )
            contract = await conn.fetchrow(
                """
                INSERT INTO contracts (
                    rfq_id, proposal_id, buyer_actor_id, supplier_actor_id,
                    title, description, agreed_amount, currency, start_date, due_date,
                    payment_type, status, created_at,
                    commission_amount, commission_percent, commission_min
                )
                VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8::contract_currency, $9, $10,
                    $11::payment_type, 'pending_payment'::contract_status, NOW(),
                    $12, $13, $14
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
                due_date,
                ptype,
                quote.amount,
                quote.percent,
                quote.minimum,
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
            commission_parts = split_commission(
                quote.amount,
                [float(item["amount"]) for item in plan_milestones],
                bank_split=bank_split,
            )
            for item, commission_part in zip(plan_milestones, commission_parts):
                await conn.execute(
                    """
                    INSERT INTO payment_milestones (
                        payment_plan_id, contract_id, title, percentage, amount,
                        trigger, status, commission_amount
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7::milestone_status, $8
                    )
                    """,
                    plan["id"],
                    contract["id"],
                    item["title"],
                    item["percentage"],
                    item["amount"],
                    item["trigger"],
                    enum_value(item["status"]),
                    commission_part,
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
            await log_event(
                conn,
                "contract.create",
                "contract",
                contract["id"],
                user_id=buyer_user_id,
                actor_id=buyer_actor_id,
                details={
                    "rfq_id": data["rfq_id"],
                    "proposal_id": data["id"],
                    "buyer_actor_id": buyer_actor_id,
                    "supplier_actor_id": data["supplier_actor_id"],
                    "amount": contract["agreed_amount"],
                    "currency": data["currency"],
                    "payment_type": ptype,
                    "milestones": len(plan_milestones),
                    "commission_amount": quote.amount,
                },
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

    async def _active_dispute_id(self, conn, contract_id: int, actor_id: int) -> int:
        row = await load_contract_row(conn, contract_id)
        await ensure_party(row, actor_id)
        dispute_id = await conn.fetchval(
            """
            SELECT id FROM disputes
            WHERE contract_id = $1 AND status::text = ANY($2::text[])
            ORDER BY id DESC
            LIMIT 1
            """,
            contract_id,
            list(ACTIVE_DISPUTE_STATUSES),
        )
        if not dispute_id:
            raise ConflictError("No active dispute for this contract")
        return dispute_id

    async def ensure_active_dispute(self, contract_id: int, actor_id: int) -> int:
        async with self.connection() as conn:
            return await self._active_dispute_id(conn, contract_id, actor_id)

    async def add_dispute_evidence(
        self,
        contract_id: int,
        actor_id: int,
        file_name: str,
        file_url: str,
        file_type: str,
        note: str | None = None,
    ):
        async with self.connection() as conn:
            dispute_id = await self._active_dispute_id(conn, contract_id, actor_id)
            await conn.execute(
                """
                INSERT INTO dispute_evidence (
                    dispute_id, uploaded_by_actor_id, file_name, file_url,
                    file_type, note, created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                """,
                dispute_id,
                actor_id,
                file_name,
                file_url,
                file_type,
                note,
            )
            await log_event(
                conn,
                "dispute.evidence.add",
                "dispute",
                dispute_id,
                actor_id=actor_id,
                details={"contract_id": contract_id, "file_name": file_name, "file_type": file_type},
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
                    "url": asset.url
                    if asset.kind == "link"
                    else storage_key_from_url(asset.url),
                    "file_type": asset.file_type,
                }
                for asset in data.assets
            ]
            file_names = data.file_names or [asset["name"] for asset in assets]
            submission_id = await conn.fetchval(
                """
                INSERT INTO work_submissions (
                    contract_id, type, note, status, submitted_at, file_names, assets
                )
                VALUES (
                    $1, $2::submission_type, $3, 'pending'::submission_status,
                    NOW(), $4::text[], $5::jsonb
                )
                RETURNING id
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
            await log_event(
                conn,
                "contract.submission.submit",
                "contract",
                contract_id,
                actor_id=supplier_id,
                details={
                    "submission_id": submission_id,
                    "type": data.type,
                    "files": len(file_names or []),
                    "before": {"status": row["status"]},
                    "after": {"status": "delivered"},
                },
            )
            result = await load_contract_schema(conn, contract_id)
        await NotificationService().notify_work_submitted(row)
        return result

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
                if row["status"] in ("cancelled", "completed"):
                    appeal_window_open = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(resolved_at), NOW() - INTERVAL '100 years')
                               > NOW() - INTERVAL '14 days'
                        FROM disputes
                        WHERE contract_id = $1 AND status::text = 'resolved'
                        """,
                        contract_id,
                    )
                    if not appeal_window_open:
                        raise ConflictError(
                            "Спор по закрытому контракту можно открыть только в течение "
                            "14 дней после решения по предыдущему спору"
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
            result = await load_contract_schema(conn, contract_id)
        if actor_id == row["buyer_actor_id"]:
            counterparty_actor_id = row["supplier_actor_id"]
            href = f"/supplier/contracts/{contract_id}"
        else:
            counterparty_actor_id = row["buyer_actor_id"]
            href = f"/customer/contracts/{contract_id}"
        await NotificationService().notify_actor_members(
            counterparty_actor_id,
            "contract",
            "Открыт спор" if status == "open" else "Подана апелляция по спору",
            f"По договору «{row['title']}» открыт спор. Добавьте своё заявление и доказательства.",
            href,
        )
        return result

    async def add_dispute_statement(
        self, contract_id: int, actor_id: int, data: DisputeStatementRequest
    ):
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            dispute_id = await self._active_dispute_id(conn, contract_id, actor_id)
            column = (
                "buyer_statement"
                if actor_id == row["buyer_actor_id"]
                else "supplier_statement"
            )
            await conn.execute(
                f"UPDATE disputes SET {column} = $1, updated_at = NOW() WHERE id = $2",
                data.text.strip(),
                dispute_id,
            )
            await log_event(
                conn,
                "dispute.statement",
                "dispute",
                dispute_id,
                actor_id=actor_id,
                details={"contract_id": contract_id, "side": column.split("_")[0]},
            )
            return await load_contract_schema(conn, contract_id)
