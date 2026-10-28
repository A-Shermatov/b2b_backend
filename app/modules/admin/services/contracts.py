from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import (
    ESCROW_AWAITING_STATUSES,
    ESCROW_LOCKED_STATUSES,
    actor_id_of,
    actor_role_of,
    apply_partial_refund,
    contract_view_sql,
    dispute_view_sql,
    escrow_pool,
    escrow_summary,
    file_url,
    load_audit_history,
    load_owners,
    log_admin,
    normalize_query,
    notify_user,
    resolve_owner_user_id,
    with_reason,
)
from app.modules.contracts.services.common import (
    file_uploader_side,
    resolve_actor_user_ids,
    sender_display_name,
)
from app.modules.finance.services import FinanceLedgerService
from app.modules.finance.services.commission import record_milestone_releases


def _shape_admin_contract_file(
    row,
    *,
    buyer_user_ids: set[int],
    supplier_user_ids: set[int],
) -> dict:
    uploaded_by = row["uploaded_by"]
    return {
        "id": row["id"],
        "file_name": row["file_name"],
        "file_url": file_url(row["file_url"]),
        "file_type": row["file_type"],
        "uploaded_by": uploaded_by,
        "uploaded_by_name": sender_display_name(
            row["first_name"],
            row["last_name"],
            row["email"],
            uploaded_by,
        ),
        "uploaded_by_side": file_uploader_side(
            uploaded_by, buyer_user_ids, supplier_user_ids
        ),
        "created_at": row["created_at"],
    }


def _contract_search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    idx = len(params)
    sql = f"""
        (
            c.title ILIKE ${idx}
            OR c.description ILIKE ${idx}
            OR c.rfq_id ILIKE ${idx}
            OR c.id::text ILIKE ${idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        sql += f" OR c.id = ${len(params)}"
    return sql + ")"


def _dispute_search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    idx = len(params)
    sql = f"""
        (
            c.title ILIKE ${idx}
            OR d.id::text ILIKE ${idx}
            OR d.contract_id::text ILIKE ${idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        sql += f" OR d.id = ${len(params)} OR d.contract_id = ${len(params)}"
    return sql + ")"


def _serialize_contract_list_item(contract, buyer, supplier, held: float) -> dict:
    return {
        "id": contract["id"],
        "title": contract["title"],
        "status": contract["status"],
        "agreed_amount": contract["agreed_amount"],
        "currency": contract["currency"],
        "payment_type": contract["payment_type"],
        "rfq_id": contract["rfq_id"],
        "proposal_id": contract["proposal_id"],
        "buyer": buyer,
        "supplier": supplier,
        "escrow_held": held,
        "created_at": contract["created_at"],
    }


def _serialize_dispute_list_item(dispute, parties) -> dict:
    return {
        "id": dispute["id"],
        "status": dispute["status"],
        "contract_id": dispute["contract_id"],
        "contract_title": dispute["contract_title"],
        "contract_amount": dispute["contract_amount"],
        "currency": dispute["currency"],
        "opened_by_actor_id": dispute["opened_by_actor_id"],
        "opened_by": (
            parties.get(dispute["opened_by_actor_id"])
            if dispute["opened_by_actor_id"]
            else None
        ),
        "buyer": parties.get(dispute["buyer_actor_id"]) if dispute["buyer_actor_id"] else None,
        "supplier": parties.get(dispute["supplier_actor_id"]) if dispute["supplier_actor_id"] else None,
        "created_at": dispute["created_at"],
        "updated_at": dispute["updated_at"],
    }


def _contract_action_notification(action: str, contract_title: str, reason: str | None):
    messages = {
        "freeze": (
            "Escrow заморожен",
            f"Администратор заморозил средства по контракту «{contract_title}».",
        ),
        "cancel": (
            "Контракт отменён",
            f"Контракт «{contract_title}» отменён администратором.",
        ),
        "force_complete": (
            "Контракт принудительно завершён",
            f"Контракт «{contract_title}» принудительно завершён администратором.",
        ),
        "open_investigation": (
            "Открыто расследование",
            f"По контракту «{contract_title}» открыто административное расследование.",
        ),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


def _dispute_action_notification(action: str, contract_title: str, reason: str | None):
    messages = {
        "release_funds": (
            "Спор: выплата исполнителю",
            f"По спору по контракту «{contract_title}» средства выплачены исполнителю.",
        ),
        "refund_buyer": (
            "Спор: возврат покупателю",
            f"По спору по контракту «{contract_title}» средства возвращены покупателю.",
        ),
        "partial_refund": (
            "Спор: частичный возврат",
            f"По спору по контракту «{contract_title}» выполнен частичный возврат.",
        ),
        "request_evidence": (
            "Спор: запрошены доказательства",
            f"Администратор запросил дополнительные доказательства по контракту «{contract_title}».",
        ),
        "close_case": (
            "Спор закрыт",
            f"Спор по контракту «{contract_title}» закрыт администратором.",
        ),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


CONTRACT_LIST_SQL = """
    SELECT
        c.id, c.title, c.status::text AS status, c.agreed_amount,
        c.currency::text AS currency, c.payment_type::text AS payment_type,
        c.rfq_id, c.proposal_id, c.buyer_actor_id, c.supplier_actor_id, c.created_at
    FROM contracts c
"""

DISPUTE_LIST_SQL = """
    SELECT
        d.id, d.status::text AS status, d.contract_id, d.opened_by_actor_id,
        d.created_at, d.updated_at,
        c.title AS contract_title, c.agreed_amount AS contract_amount,
        c.currency::text AS currency,
        c.buyer_actor_id, c.supplier_actor_id
    FROM disputes d
    JOIN contracts c ON c.id = d.contract_id
"""


async def _load_milestones(conn, contract_id: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT m.id, m.title, m.percentage, m.amount, m.trigger,
               m.status::text AS status
        FROM payment_milestones m
        JOIN payment_plans p ON p.id = m.payment_plan_id
        WHERE p.contract_id = $1
        ORDER BY m.id
        """,
        contract_id,
    )
    return [dict(row) for row in rows]


async def _cancel_marketplace_or_conflict(contract_id: int, reason: str) -> None:
    from app.modules.payments.gateways.alif import AlifGatewayError
    from app.modules.payments.services import PaymentService

    try:
        await PaymentService().cancel_marketplace_for_contract(contract_id, reason=reason)
    except AlifGatewayError as exc:
        raise ConflictError(
            f"Не удалось отменить платёж в Alif, возврат не выполнен: {exc.detail}"
        ) from exc


async def _refund_escrow_to_buyer(
    conn,
    *,
    contract_id: int,
    milestones: list[dict],
    currency: str,
    buyer_actor_id: int,
    description: str,
    metadata: dict,
) -> float:
    refund_amount = escrow_pool(milestones)
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE payment_milestones m
            SET status = CASE
                WHEN m.status::text = ANY($3::text[]) THEN 'cancelled'::milestone_status
                ELSE 'refunded'::milestone_status
            END
            FROM payment_plans p
            WHERE m.payment_plan_id = p.id
              AND p.contract_id = $1
              AND m.status::text = ANY($2::text[])
            """,
            contract_id,
            list(ESCROW_LOCKED_STATUSES + ESCROW_AWAITING_STATUSES),
            list(ESCROW_AWAITING_STATUSES),
        )
        if refund_amount > 0:
            await FinanceLedgerService(conn).record(
                payment_type="refund",
                amount=refund_amount,
                currency=currency,
                title=f"Возврат · контракт #{contract_id}",
                description=description,
                status="paid",
                gateway="manual",
                actor_id=buyer_actor_id,
                contract_id=contract_id,
                metadata=metadata,
            )
    return refund_amount


class AdminContractsMixin:
    async def list_contracts(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "active",
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _contract_search_sql(normalized, search_params)

        async def _count(conn, extra_sql: str | None):
            clauses = [search_sql] if search_sql else []
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(await conn.fetchval(f"SELECT COUNT(*) FROM contracts c {where}", *search_params))

        async with self.connection() as conn:
            view_sql = contract_view_sql(view)
            total = await _count(conn, view_sql)
            count_values = {}
            for count_view in ("active", "completed", "cancelled", "disputed"):
                count_values[count_view] = await _count(conn, contract_view_sql(count_view))
            params = list(search_params)
            clauses = [search_sql] if search_sql else []
            if view_sql:
                clauses.append(view_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            items = await conn.fetch(
                f"""
                {CONTRACT_LIST_SQL}
                {where}
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            parties = await load_owners(
                conn,
                [c["buyer_actor_id"] for c in items] + [c["supplier_actor_id"] for c in items],
            )
            serialized = []
            for item in items:
                milestones = await _load_milestones(conn, item["id"])
                escrow = escrow_summary(milestones, item["currency"])
                serialized.append(
                    _serialize_contract_list_item(
                        item,
                        parties.get(item["buyer_actor_id"]),
                        parties.get(item["supplier_actor_id"]),
                        escrow["held"],
                    )
                )

        return {
            "items": serialized,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_contract_detail(self, contract_id: int) -> dict:
        async with self.connection() as conn:
            contract = await conn.fetchrow(
                f"{CONTRACT_LIST_SQL} WHERE c.id = $1",
                contract_id,
            )
            if not contract:
                raise NotFoundError("Contract not found")
            extra = await conn.fetchrow(
                """
                SELECT c.description, c.start_date, c.due_date,
                       rfq.id AS rfq_id, rfq.title AS rfq_title, rfq.status::text AS rfq_status,
                       p.id AS proposal_id, p.price AS proposal_price, p.status::text AS proposal_status,
                       pp.id AS payment_plan_id, pp.payment_type::text AS plan_payment_type
                FROM contracts c
                LEFT JOIN rfqs rfq ON rfq.id = c.rfq_id
                LEFT JOIN proposals p ON p.id = c.proposal_id
                LEFT JOIN payment_plans pp ON pp.contract_id = c.id
                WHERE c.id = $1
                """,
                contract_id,
            )
            milestones = await _load_milestones(conn, contract_id)
            files = await conn.fetch(
                """
                SELECT
                    cf.id, cf.file_name, cf.file_url, cf.file_type,
                    cf.uploaded_by, cf.created_at,
                    u.first_name, u.last_name, u.email
                FROM contract_files cf
                LEFT JOIN users u ON u.id = cf.uploaded_by
                WHERE cf.contract_id = $1
                ORDER BY cf.created_at DESC
                """,
                contract_id,
            )
            buyer_user_ids = set(
                await resolve_actor_user_ids(conn, contract["buyer_actor_id"])
            )
            supplier_user_ids = set(
                await resolve_actor_user_ids(conn, contract["supplier_actor_id"])
            )
            messages = await conn.fetch(
                """
                SELECT m.id, m.sender_id, m.text, m.created_at
                FROM conversations conv
                JOIN messages m ON m.conversation_id = conv.id
                WHERE conv.contract_id = $1
                ORDER BY m.created_at DESC
                """,
                contract_id,
            )
            parties = await load_owners(
                conn, [contract["buyer_actor_id"], contract["supplier_actor_id"]]
            )
            buyer = parties.get(contract["buyer_actor_id"])
            supplier = parties.get(contract["supplier_actor_id"])
            history = await load_audit_history(conn, "contract", str(contract_id))

        escrow = escrow_summary(milestones, contract["currency"])
        payload = _serialize_contract_list_item(contract, buyer, supplier, escrow["held"])
        payload.update(
            {
                "description": extra["description"],
                "start_date": extra["start_date"],
                "due_date": extra["due_date"],
                "payment_type": contract["payment_type"],
                "buyer": buyer,
                "supplier": supplier,
                "rfq": (
                    {
                        "id": extra["rfq_id"],
                        "title": extra["rfq_title"],
                        "status": extra["rfq_status"],
                    }
                    if extra["rfq_id"]
                    else None
                ),
                "proposal": (
                    {
                        "id": extra["proposal_id"],
                        "price": extra["proposal_price"],
                        "status": extra["proposal_status"],
                    }
                    if extra["proposal_id"]
                    else None
                ),
                "payment_plan": (
                    {
                        "id": extra["payment_plan_id"],
                        "payment_type": extra["plan_payment_type"],
                    }
                    if extra["payment_plan_id"]
                    else None
                ),
                "milestones": milestones,
                "files": [
                    _shape_admin_contract_file(
                        row,
                        buyer_user_ids=buyer_user_ids,
                        supplier_user_ids=supplier_user_ids,
                    )
                    for row in files
                ],
                "messages": [dict(row) for row in messages],
                "escrow": escrow,
                "history": history,
            }
        )
        return payload

    async def apply_contract_action(
        self,
        contract_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        allowed = {"freeze", "cancel", "force_complete", "open_investigation"}
        if action not in allowed:
            raise ValidationError("Unsupported contract action")
        ensure_permission(
            current_user,
            "admin_contracts.force_complete"
            if action == "force_complete"
            else "admin_contracts.update",
        )
        if action in {"cancel", "force_complete", "open_investigation"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        freeze_statuses = {
            "funded",
            "in_progress",
            "submitted",
            "approved",
        }
        from app.modules.payments.services import PaymentService

        if action == "force_complete":
            await PaymentService().confirm_marketplace_deliveries_for_contract(contract_id)
        elif action == "cancel":
            async with self.connection() as conn:
                current_status = await conn.fetchval(
                    "SELECT status::text FROM contracts WHERE id = $1", contract_id
                )
            if current_status is None:
                raise NotFoundError("Contract not found")
            if current_status in {"completed", "cancelled"}:
                raise ConflictError("Contract is already closed")
            await _cancel_marketplace_or_conflict(
                contract_id, (reason or "Отмена сделки").strip()
            )

        async with self.connection() as conn:
            contract = await conn.fetchrow(
                """
                SELECT id, title, status::text AS status, buyer_actor_id, supplier_actor_id,
                       currency::text AS currency
                FROM contracts WHERE id = $1
                """,
                contract_id,
            )
            if not contract:
                raise NotFoundError("Contract not found")
            previous_status = contract["status"]
            status = previous_status
            milestones = await _load_milestones(conn, contract_id)

            if action == "freeze":
                await conn.execute(
                    """
                    UPDATE payment_milestones m
                    SET status = 'disputed'::milestone_status
                    FROM payment_plans p
                    WHERE m.payment_plan_id = p.id
                      AND p.contract_id = $1
                      AND m.status::text = ANY($2::text[])
                    """,
                    contract_id,
                    list(freeze_statuses),
                )
            elif action == "cancel":
                if contract["status"] in {"completed", "cancelled"}:
                    raise ConflictError("Contract is already closed")
                status = "cancelled"
                await _refund_escrow_to_buyer(
                    conn,
                    contract_id=contract_id,
                    milestones=milestones,
                    currency=contract["currency"],
                    buyer_actor_id=contract["buyer_actor_id"],
                    description=reason.strip(),
                    metadata={"action": "cancel"},
                )
            elif action == "force_complete":
                if contract["status"] == "completed":
                    raise ConflictError("Contract is already completed")
                status = "completed"
                async with conn.transaction():
                    released = await conn.fetch(
                        """
                        UPDATE payment_milestones m
                        SET status = 'released'::milestone_status
                        FROM payment_plans p
                        WHERE m.payment_plan_id = p.id
                          AND p.contract_id = $1
                          AND m.status::text = 'funded'
                        RETURNING m.id
                        """,
                        contract_id,
                    )
                    await record_milestone_releases(conn, [item["id"] for item in released])
            elif action == "open_investigation":
                if contract["status"] in {"completed", "cancelled"}:
                    raise ConflictError("Cannot open investigation on a closed contract")
                active = await conn.fetchval(
                    """
                    SELECT id FROM disputes
                    WHERE contract_id = $1
                      AND status::text IN ('open', 'under_review', 'appealed')
                    LIMIT 1
                    """,
                    contract_id,
                )
                if active:
                    raise ConflictError("An active dispute already exists for this contract")
                had_resolved = await conn.fetchval(
                    """
                    SELECT 1 FROM disputes
                    WHERE contract_id = $1 AND status::text = 'resolved'
                    LIMIT 1
                    """,
                    contract_id,
                )
                dispute_status = "appealed" if had_resolved else "open"
                dispute = await conn.fetchrow(
                    """
                    INSERT INTO disputes (
                        contract_id, status, opened_by_actor_id,
                        buyer_statement, created_at, updated_at
                    )
                    VALUES ($1, $2::dispute_status, NULL, $3, NOW(), NOW())
                    RETURNING id, status::text AS status
                    """,
                    contract_id,
                    dispute_status,
                    reason.strip() if reason else None,
                )
                status = "disputed"
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "dispute.open",
                    "dispute",
                    dispute["id"],
                    {
                        "contract_id": contract_id,
                        "status": dispute["status"],
                        "reason": reason.strip() if reason else None,
                        "source": "admin.open_investigation",
                    },
                )

            if status != previous_status:
                await conn.execute(
                    "UPDATE contracts SET status = $1::contract_status WHERE id = $2",
                    status,
                    contract_id,
                )

            for party_id in (contract["buyer_actor_id"], contract["supplier_actor_id"]):
                user_id = await resolve_owner_user_id(conn, party_id)
                if not user_id:
                    continue
                title, body = _contract_action_notification(action, contract["title"], reason)
                await notify_user(
                    conn,
                    user_id,
                    title,
                    body,
                    href=f"/customer/contracts/{contract_id}",
                )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.contract.{action}",
                "contract",
                contract_id,
                {
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": status,
                },
            )

        return {"id": contract_id, "action": action, "status": status}

    async def list_disputes(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "open",
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _dispute_search_sql(normalized, search_params)
        join_sql = "FROM disputes d JOIN contracts c ON c.id = d.contract_id"

        async def _count(conn, extra_sql: str | None):
            clauses = [search_sql] if search_sql else []
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(await conn.fetchval(f"SELECT COUNT(*) {join_sql} {where}", *search_params))

        async with self.connection() as conn:
            view_sql = dispute_view_sql(view)
            total = await _count(conn, view_sql)
            count_values = {}
            for count_view in ("open", "under_review", "resolved", "appealed"):
                count_values[count_view] = await _count(conn, dispute_view_sql(count_view))
            params = list(search_params)
            clauses = [search_sql] if search_sql else []
            if view_sql:
                clauses.append(view_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            items = await conn.fetch(
                f"""
                {DISPUTE_LIST_SQL}
                {where}
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            actor_ids = []
            for item in items:
                actor_ids.extend([item["buyer_actor_id"], item["supplier_actor_id"]])
                if item["opened_by_actor_id"]:
                    actor_ids.append(item["opened_by_actor_id"])
            parties = await load_owners(conn, actor_ids)

        return {
            "items": [_serialize_dispute_list_item(item, parties) for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_active_dispute_for_evidence(self, dispute_id: int) -> None:
        async with self.connection() as conn:
            status = await conn.fetchval(
                "SELECT status::text FROM disputes WHERE id = $1", dispute_id
            )
        if status is None:
            raise NotFoundError("Dispute not found")
        if status == "resolved":
            raise ConflictError("Dispute is already resolved")

    async def upload_dispute_evidence(
        self,
        dispute_id: int,
        current_user,
        file_name: str,
        file_url: str,
        file_type: str,
        note: str | None = None,
    ) -> dict:
        ensure_permission(current_user, "admin_disputes.update")
        async with self.connection() as conn:
            status = await conn.fetchval(
                "SELECT status::text FROM disputes WHERE id = $1", dispute_id
            )
            if status is None:
                raise NotFoundError("Dispute not found")
            if status == "resolved":
                raise ConflictError("Dispute is already resolved")
            user_id = actor_id_of(current_user)
            actor_id = await conn.fetchval(
                "SELECT id FROM actors WHERE user_id = $1 ORDER BY id LIMIT 1",
                user_id,
            )
            admin_note = note
            if actor_id is None:
                prefix = f"[admin:{user_id}]"
                admin_note = f"{prefix} {note}".strip() if note else prefix
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
                admin_note,
            )
        return await self.get_dispute_detail(dispute_id)

    async def get_dispute_detail(self, dispute_id: int) -> dict:
        async with self.connection() as conn:
            dispute = await conn.fetchrow(
                f"{DISPUTE_LIST_SQL} WHERE d.id = $1",
                dispute_id,
            )
            if not dispute:
                raise NotFoundError("Dispute not found")
            extra = await conn.fetchrow(
                """
                SELECT d.buyer_statement, d.supplier_statement, d.admin_instructions,
                       d.resolution::text AS resolution, d.resolution_note,
                       d.partial_buyer_amount, d.resolved_at,
                       c.id AS contract_id, c.title, c.status::text AS contract_status,
                       c.agreed_amount, c.currency::text AS currency,
                       c.rfq_id, c.proposal_id, c.description
                FROM disputes d
                JOIN contracts c ON c.id = d.contract_id
                WHERE d.id = $1
                """,
                dispute_id,
            )
            evidence = await conn.fetch(
                """
                SELECT id, file_name, file_url, file_type, note,
                       uploaded_by_actor_id, created_at
                FROM dispute_evidence
                WHERE dispute_id = $1
                ORDER BY created_at DESC
                """,
                dispute_id,
            )
            files = await conn.fetch(
                """
                SELECT
                    cf.id, cf.file_name, cf.file_url, cf.file_type,
                    cf.uploaded_by, cf.created_at,
                    u.first_name, u.last_name, u.email
                FROM contract_files cf
                LEFT JOIN users u ON u.id = cf.uploaded_by
                WHERE cf.contract_id = $1
                ORDER BY cf.created_at DESC
                """,
                extra["contract_id"],
            )
            buyer_user_ids = set(
                await resolve_actor_user_ids(conn, dispute["buyer_actor_id"])
            )
            supplier_user_ids = set(
                await resolve_actor_user_ids(conn, dispute["supplier_actor_id"])
            )
            conversation_id = await conn.fetchval(
                "SELECT id FROM conversations WHERE contract_id = $1",
                extra["contract_id"],
            )
            messages = await conn.fetch(
                """
                SELECT m.id, m.sender_id, m.text, m.created_at
                FROM conversations conv
                JOIN messages m ON m.conversation_id = conv.id
                WHERE conv.contract_id = $1
                  AND m.deleted_at IS NULL
                ORDER BY m.created_at DESC
                """,
                extra["contract_id"],
            )
            milestones = await _load_milestones(conn, extra["contract_id"])
            actor_ids = [dispute["buyer_actor_id"], dispute["supplier_actor_id"]]
            if dispute["opened_by_actor_id"]:
                actor_ids.append(dispute["opened_by_actor_id"])
            parties = await load_owners(conn, actor_ids)
            history = await load_audit_history(conn, "dispute", str(dispute_id))

        escrow = escrow_summary(milestones, extra["currency"])
        payload = _serialize_dispute_list_item(dispute, parties)
        payload.update(
            {
                "buyer_statement": extra["buyer_statement"],
                "supplier_statement": extra["supplier_statement"],
                "admin_instructions": extra["admin_instructions"],
                "resolution": extra["resolution"],
                "resolution_note": extra["resolution_note"],
                "partial_buyer_amount": extra["partial_buyer_amount"],
                "resolved_at": extra["resolved_at"],
                "buyer": parties.get(dispute["buyer_actor_id"]),
                "supplier": parties.get(dispute["supplier_actor_id"]),
                "contract": {
                    "id": extra["contract_id"],
                    "title": extra["title"],
                    "status": extra["contract_status"],
                    "agreed_amount": extra["agreed_amount"],
                    "currency": extra["currency"],
                    "rfq_id": extra["rfq_id"],
                    "proposal_id": extra["proposal_id"],
                    "description": extra["description"],
                },
                "evidence": [
                    {
                        "id": row["id"],
                        "file_name": row["file_name"],
                        "file_url": file_url(row["file_url"]),
                        "file_type": row["file_type"],
                        "note": row["note"],
                        "uploaded_by_actor_id": row["uploaded_by_actor_id"],
                        "created_at": row["created_at"],
                    }
                    for row in evidence
                ],
                "files": [
                    _shape_admin_contract_file(
                        row,
                        buyer_user_ids=buyer_user_ids,
                        supplier_user_ids=supplier_user_ids,
                    )
                    for row in files
                ],
                "messages": [dict(row) for row in messages],
                "escrow": escrow,
                "timeline": history,
                "conversation_id": conversation_id,
                "chat_id": f"contract:{conversation_id}" if conversation_id else None,
            }
        )
        return payload

    async def apply_dispute_action(
        self,
        dispute_id: int,
        action: str,
        current_user,
        reason: str | None = None,
        partial_buyer_amount: float | None = None,
    ) -> dict:
        allowed = {
            "release_funds",
            "refund_buyer",
            "partial_refund",
            "request_evidence",
            "close_case",
        }
        if action not in allowed:
            raise ValidationError("Unsupported dispute action")
        ensure_permission(current_user, "admin_disputes.update")
        if not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        movable = set(ESCROW_LOCKED_STATUSES)
        from app.modules.payments.services import PaymentService

        if action == "release_funds":
            async with self.connection() as conn:
                mp_contract_id = await conn.fetchval(
                    "SELECT contract_id FROM disputes WHERE id = $1", dispute_id
                )
            if mp_contract_id:
                await PaymentService().confirm_marketplace_deliveries_for_contract(mp_contract_id)
        elif action == "refund_buyer":
            async with self.connection() as conn:
                pre = await conn.fetchrow(
                    "SELECT status::text AS status, contract_id FROM disputes WHERE id = $1",
                    dispute_id,
                )
            if not pre:
                raise NotFoundError("Dispute not found")
            if pre["status"] == "resolved":
                raise ConflictError("Dispute is already resolved")
            await _cancel_marketplace_or_conflict(
                pre["contract_id"], reason.strip() or "Возврат покупателю"
            )

        async with self.connection() as conn:
            dispute = await conn.fetchrow(
                """
                SELECT d.id, d.status::text AS status, d.contract_id,
                       c.title, c.status::text AS contract_status,
                       c.currency::text AS currency,
                       c.buyer_actor_id, c.supplier_actor_id
                FROM disputes d
                JOIN contracts c ON c.id = d.contract_id
                WHERE d.id = $1
                """,
                dispute_id,
            )
            if not dispute:
                raise NotFoundError("Dispute not found")
            if dispute["status"] == "resolved":
                raise ConflictError("Dispute is already resolved")

            previous_status = dispute["status"]
            milestones = await _load_milestones(conn, dispute["contract_id"])
            resolution = None
            contract_status = dispute["contract_status"]
            dispute_status = previous_status

            if action == "request_evidence":
                dispute_status = "under_review"
                await conn.execute(
                    """
                    UPDATE disputes
                    SET status = 'under_review'::dispute_status,
                        admin_instructions = $1,
                        updated_at = NOW()
                    WHERE id = $2
                    """,
                    reason.strip(),
                    dispute_id,
                )
            elif action == "release_funds":
                dispute_status = "resolved"
                resolution = "release_funds"
                contract_status = "completed"
                async with conn.transaction():
                    released = await conn.fetch(
                        """
                        UPDATE payment_milestones m
                        SET status = 'released'::milestone_status
                        FROM payment_plans p
                        WHERE m.payment_plan_id = p.id
                          AND p.contract_id = $1
                          AND m.status::text = ANY($2::text[])
                        RETURNING m.id
                        """,
                        dispute["contract_id"],
                        list(movable),
                    )
                    await record_milestone_releases(conn, [item["id"] for item in released])
            elif action == "refund_buyer":
                dispute_status = "resolved"
                resolution = "refund_buyer"
                contract_status = "cancelled"
                await _refund_escrow_to_buyer(
                    conn,
                    contract_id=dispute["contract_id"],
                    milestones=milestones,
                    currency=dispute["currency"],
                    buyer_actor_id=dispute["buyer_actor_id"],
                    description=reason.strip(),
                    metadata={"dispute_id": dispute_id, "action": "refund_buyer"},
                )
            elif action == "partial_refund":
                if partial_buyer_amount is None or partial_buyer_amount <= 0:
                    raise ValidationError("partial_buyer_amount is required")
                pool = escrow_pool(milestones, movable)
                if partial_buyer_amount > pool + 1e-6:
                    raise ValidationError("partial_buyer_amount exceeds escrow pool")
                previous_statuses = {m["id"]: m["status"] for m in milestones}
                amounts = ", ".join(
                    f"{float(m['amount']):g}" for m in milestones if m["status"] in movable
                )
                actual_refund = apply_partial_refund(milestones, movable, partial_buyer_amount)
                if actual_refund <= 1e-9:
                    raise ValidationError(
                        "Сумма возврата должна покрывать хотя бы один milestone целиком. "
                        f"Суммы milestone в escrow: {amounts}"
                    )
                partial_buyer_amount = actual_refund
                async with conn.transaction():
                    for milestone in milestones:
                        await conn.execute(
                            "UPDATE payment_milestones SET status = $1::milestone_status WHERE id = $2",
                            milestone["status"],
                            milestone["id"],
                        )
                    await record_milestone_releases(
                        conn,
                        [
                            m["id"]
                            for m in milestones
                            if m["status"] == "released" and previous_statuses[m["id"]] != "released"
                        ],
                    )
                dispute_status = "resolved"
                resolution = "partial_refund"
                contract_status = "completed"
                await FinanceLedgerService(conn).record(
                    payment_type="refund",
                    amount=partial_buyer_amount,
                    currency=dispute["currency"],
                    title=f"Частичный возврат · контракт #{dispute['contract_id']}",
                    description=reason.strip(),
                    status="paid",
                    gateway="manual",
                    actor_id=dispute["buyer_actor_id"],
                    contract_id=dispute["contract_id"],
                    metadata={"dispute_id": dispute_id, "action": "partial_refund"},
                )
            elif action == "close_case":
                if escrow_pool(milestones, movable) > 1e-6:
                    raise ValidationError(
                        "Сначала урегулируйте escrow: выплата, возврат или частичный возврат"
                    )
                dispute_status = "resolved"
                resolution = "close_case"
                contract_status = "completed"

            if action != "request_evidence":
                await conn.execute(
                    """
                    UPDATE disputes
                    SET status = $1::dispute_status,
                        resolution = $2::dispute_resolution,
                        resolution_note = $3,
                        partial_buyer_amount = $4,
                        resolved_at = NOW(),
                        resolved_by_id = $5,
                        updated_at = NOW()
                    WHERE id = $6
                    """,
                    dispute_status,
                    resolution,
                    reason.strip(),
                    partial_buyer_amount if action == "partial_refund" else None,
                    actor_id_of(current_user),
                    dispute_id,
                )
                await conn.execute(
                    "UPDATE contracts SET status = $1::contract_status WHERE id = $2",
                    contract_status,
                    dispute["contract_id"],
                )

            for party_id in (dispute["buyer_actor_id"], dispute["supplier_actor_id"]):
                user_id = await resolve_owner_user_id(conn, party_id)
                if not user_id:
                    continue
                title, body = _dispute_action_notification(action, dispute["title"], reason)
                await notify_user(
                    conn,
                    user_id,
                    title,
                    body,
                    href=f"/customer/contracts/{dispute['contract_id']}",
                )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.dispute.{action}",
                "dispute",
                dispute_id,
                {
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": dispute_status,
                    "partial_buyer_amount": partial_buyer_amount,
                    "contract_id": dispute["contract_id"],
                },
            )

        return {
            "id": dispute_id,
            "action": action,
            "status": dispute_status,
            "resolution": resolution,
            "contract_status": contract_status,
            "partial_buyer_amount": (
                partial_buyer_amount if action == "partial_refund" else None
            ),
        }
