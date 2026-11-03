from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.contracts.services.common import (
    ensure_not_under_dispute,
    ensure_party,
    load_contract_row,
    load_contract_schema,
    message_to_dict,
    message_ws_payload,
    resolve_actor_user_ids,
)
from app.modules.finance.services.commission import record_milestone_releases
from app.modules.notifications.services import NotificationService
from app.modules.notifications.ws_manager import notification_ws_manager
from app.utils.audit import log_event


class ContractUpdateMixin:
    async def _broadcast_message_event(self, conn, row, actor_id: int, event: str, payload_data: dict):
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

    async def _load_message(self, conn, conversation_id: int, message_id: int):
        row = await conn.fetchrow(
            """
            SELECT
                m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
                m.status::text AS status, m.delivered_at, m.viewed_at,
                u.first_name, u.last_name, u.email
            FROM messages m
            LEFT JOIN users u ON u.id = m.sender_id
            WHERE m.id = $1 AND m.conversation_id = $2
            """,
            message_id,
            conversation_id,
        )
        if not row:
            raise NotFoundError("Message not found")
        return row

    async def mark_message_delivered(
        self, contract_id: int, message_id: int, user_id: int, actor_id: int
    ):
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await ensure_party(row, actor_id)
            conversation_id = await conn.fetchval(
                "SELECT id FROM conversations WHERE contract_id = $1",
                contract_id,
            )
            if conversation_id is None:
                raise NotFoundError("Conversation not found")
            msg = await self._load_message(conn, conversation_id, message_id)
            if msg["sender_id"] == user_id:
                raise ForbiddenError("Cannot acknowledge own message")
            if msg["status"] == "sent":
                await conn.execute(
                    """
                    UPDATE messages
                    SET status = 'delivered'::message_delivery_status, delivered_at = NOW()
                    WHERE id = $1
                    """,
                    message_id,
                )
                msg = await self._load_message(conn, conversation_id, message_id)
                await self._broadcast_message_event(
                    conn,
                    row,
                    actor_id,
                    "contract.message.status",
                    {
                        "contract_id": contract_id,
                        "message": message_ws_payload(msg),
                    },
                )
            return message_to_dict(msg)

    async def mark_messages_read(self, contract_id: int, user_id: int, actor_id: int):
        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await ensure_party(row, actor_id)
            conversation_id = await conn.fetchval(
                "SELECT id FROM conversations WHERE contract_id = $1",
                contract_id,
            )
            if conversation_id is None:
                return {"message_ids": []}
            updated_ids = await conn.fetch(
                """
                UPDATE messages
                SET
                    delivered_at = COALESCE(delivered_at, NOW()),
                    status = 'viewed'::message_delivery_status,
                    viewed_at = NOW()
                WHERE conversation_id = $1
                  AND sender_id <> $2
                  AND status::text <> 'viewed'
                RETURNING id
                """,
                conversation_id,
                user_id,
            )
            if not updated_ids:
                return {"message_ids": []}
            payloads = []
            for item in updated_ids:
                msg = await self._load_message(conn, conversation_id, item["id"])
                payloads.append(message_ws_payload(msg))
            await self._broadcast_message_event(
                conn,
                row,
                actor_id,
                "contract.message.status",
                {
                    "contract_id": contract_id,
                    "messages": payloads,
                    "message": payloads[0],
                },
            )
            return {"message_ids": [item["id"] for item in updated_ids], "messages": payloads}

    async def _ensure_submission_reviewable(
        self,
        conn,
        row,
        submission_id: int,
        buyer_id: int,
        *,
        require_funded: bool = False,
        after_payout: bool = False,
    ) -> None:
        if row["buyer_actor_id"] != buyer_id:
            raise ForbiddenError("Access denied")
        if not after_payout:
            await ensure_not_under_dispute(conn, row)
        allowed = ("active", "delivered", "disputed") if after_payout else ("active", "delivered")
        if row["status"] not in allowed:
            raise ConflictError("Contract is not active")
        sub = await conn.fetchrow(
            "SELECT id, status::text AS status FROM work_submissions WHERE id = $1 AND contract_id = $2",
            submission_id,
            row["id"],
        )
        if not sub:
            raise NotFoundError("Submission not found")
        if sub["status"] != "pending":
            raise ConflictError("Submission already reviewed")
        # Escrow: the buyer pays into the bank hold first and accepting the work releases
        # it. Accepting unpaid work would close the contract with nothing to release.
        if require_funded and await conn.fetchval(
            """
            SELECT 1 FROM payment_milestones
            WHERE contract_id = $1 AND status::text IN ('pending', 'awaiting_payment')
            LIMIT 1
            """,
            row["id"],
        ):
            raise ConflictError(
                "Сначала оплатите работу: деньги будут удерживаться банком до вашей приёмки"
            )

    async def approve_submission(self, contract_id: int, submission_id: int, buyer_id: int):
        from app.modules.payments.services import PaymentService

        async with self.connection() as conn:
            row = await load_contract_row(conn, contract_id)
            await self._ensure_submission_reviewable(
                conn, row, submission_id, buyer_id, require_funded=True
            )

        # Release Alif Marketplace hold before marking milestones released locally.
        await PaymentService().confirm_marketplace_deliveries_for_contract(contract_id)

        async with self.connection() as conn:
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT id FROM contracts WHERE id = $1 FOR UPDATE", contract_id
                )
                row = await load_contract_row(conn, contract_id)
                # The bank has already paid the supplier: a dispute opened meanwhile
                # must not keep the paid milestones "funded" in our books.
                await self._ensure_submission_reviewable(
                    conn, row, submission_id, buyer_id, after_payout=True
                )
                await conn.execute(
                    """
                    UPDATE work_submissions
                    SET status = 'accepted'::submission_status
                    WHERE id = $1
                    """,
                    submission_id,
                )
                await conn.execute(
                    "UPDATE contracts SET status = 'completed'::contract_status WHERE id = $1",
                    contract_id,
                )
                await conn.execute(
                    "UPDATE rfqs SET status = 'completed'::rfq_status WHERE id = $1",
                    row["rfq_id"],
                )
                released = await conn.fetch(
                    """
                    UPDATE payment_milestones
                    SET status = 'released'::milestone_status
                    WHERE contract_id = $1 AND status::text = 'funded'
                    RETURNING id
                    """,
                    contract_id,
                )
                await record_milestone_releases(conn, [item["id"] for item in released])
                await conn.execute(
                    """
                    UPDATE payment_milestones
                    SET status = 'awaiting_payment'::milestone_status
                    WHERE contract_id = $1 AND status::text = 'pending'
                    """,
                    contract_id,
                )
                await log_event(
                    conn,
                    "contract.submission.approve",
                    "contract",
                    contract_id,
                    actor_id=buyer_id,
                    details={"submission_id": submission_id},
                )
                await log_event(
                    conn,
                    "contract.complete",
                    "contract",
                    contract_id,
                    actor_id=buyer_id,
                    details={
                        "before": {"status": row["status"]},
                        "after": {"status": "completed"},
                        "released_milestone_ids": [item["id"] for item in released],
                    },
                )
            result = await load_contract_schema(conn, contract_id)
        await NotificationService().notify_contract_closed(row)
        return result

    async def reject_submission(self, contract_id: int, submission_id: int, buyer_id: int):
        async with self.connection() as conn:
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT id FROM contracts WHERE id = $1 FOR UPDATE", contract_id
                )
                row = await load_contract_row(conn, contract_id)
                await self._ensure_submission_reviewable(conn, row, submission_id, buyer_id)
                await conn.execute(
                    "UPDATE work_submissions SET status = 'rejected'::submission_status WHERE id = $1",
                    submission_id,
                )
                await conn.execute(
                    "UPDATE contracts SET status = 'active'::contract_status WHERE id = $1",
                    contract_id,
                )
                await log_event(
                    conn,
                    "contract.submission.reject",
                    "contract",
                    contract_id,
                    actor_id=buyer_id,
                    details={
                        "submission_id": submission_id,
                        "before": {"status": row["status"]},
                        "after": {"status": "active"},
                    },
                )
            result = await load_contract_schema(conn, contract_id)
        await NotificationService().notify_submission_rejected(row)
        return result
