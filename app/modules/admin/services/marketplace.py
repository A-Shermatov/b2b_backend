from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import (
    actor_id_of,
    actor_role_of,
    file_url,
    load_audit_history,
    load_owners,
    log_admin,
    normalize_query,
    notify_user,
    proposal_view_sql,
    resolve_owner_user_id,
    rfq_view_sql,
    with_reason,
)


def _rfq_search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    idx = len(params)
    return f"""
        (
            r.title ILIKE ${idx}
            OR r.description ILIKE ${idx}
            OR r.id ILIKE ${idx}
            OR r.category_id ILIKE ${idx}
        )
    """


def _proposal_search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    idx = len(params)
    sql = f"""
        (
            p.message ILIKE ${idx}
            OR p.id::text ILIKE ${idx}
            OR p.rfq_id ILIKE ${idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        sql += f" OR p.id = ${len(params)}"
    return sql + ")"


def _serialize_rfq_list_item(rfq, buyer) -> dict:
    return {
        "id": rfq["id"],
        "title": rfq["title"],
        "type": rfq["type"],
        "status": rfq["status"],
        "category_id": rfq["category_id"],
        "currency": rfq["currency"],
        "budget_from": rfq["budget_from"],
        "budget_to": rfq["budget_to"],
        "deadline": rfq["deadline"],
        "proposals_count": rfq["proposals_count"],
        "open_reports_count": rfq["open_reports_count"],
        "buyer": buyer,
        "created_at": rfq["created_at"],
        "updated_at": rfq["updated_at"],
    }


def _serialize_proposal_list_item(proposal, supplier, buyer) -> dict:
    return {
        "id": proposal["id"],
        "rfq_id": proposal["rfq_id"],
        "rfq_title": proposal["rfq_title"],
        "price": proposal["price"],
        "currency": proposal["currency"],
        "status": proposal["status"],
        "open_reports_count": proposal["open_reports_count"],
        "has_contract": proposal["has_contract"],
        "supplier": supplier,
        "buyer": buyer,
        "created_at": proposal["created_at"],
    }


def _rfq_action_notification(action: str, rfq_title: str, reason: str | None):
    messages = {
        "hide": ("Заявка скрыта", f"Заявка «{rfq_title}» скрыта администратором платформы."),
        "close": ("Заявка закрыта", f"Заявка «{rfq_title}» закрыта администратором платформы."),
        "delete": ("Заявка удалена", f"Заявка «{rfq_title}» удалена администратором платформы."),
        "warn_buyer": (
            "Предупреждение по заявке",
            f"Администратор отправил предупреждение по заявке «{rfq_title}».",
        ),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


RFQ_LIST_SQL = """
    SELECT
        r.id, r.title, r.type::text AS type, r.status::text AS status,
        r.category_id, r.currency, r.budget_from, r.budget_to, r.deadline,
        r.actor_id, r.created_at, r.updated_at,
        (SELECT COUNT(*) FROM proposals p WHERE p.rfq_id = r.id) AS proposals_count,
        (
            SELECT COUNT(*) FROM rfq_reports rr
            WHERE rr.rfq_id = r.id AND rr.status::text = 'open'
        ) AS open_reports_count
    FROM rfqs r
"""

PROPOSAL_LIST_SQL = """
    SELECT
        p.id, p.rfq_id, p.price, p.currency::text AS currency,
        p.status::text AS status, p.supplier_actor_id, p.created_at,
        rfq.title AS rfq_title, rfq.actor_id AS buyer_actor_id,
        EXISTS (SELECT 1 FROM contracts c WHERE c.proposal_id = p.id) AS has_contract,
        (
            SELECT COUNT(*) FROM proposal_reports pr
            WHERE pr.proposal_id = p.id AND pr.status::text = 'open'
        ) AS open_reports_count
    FROM proposals p
    LEFT JOIN rfqs rfq ON rfq.id = p.rfq_id
"""


class AdminMarketplaceMixin:
    async def list_rfqs(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "published",
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _rfq_search_sql(normalized, search_params)

        async def _count(conn, extra_sql: str | None):
            clauses = [search_sql] if search_sql else []
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(await conn.fetchval(f"SELECT COUNT(*) FROM rfqs r {where}", *search_params))

        async with self.connection() as conn:
            view_sql = rfq_view_sql(view)
            total = await _count(conn, view_sql)
            count_values = {}
            for count_view in ("published", "closed", "draft", "reported", "archived"):
                count_values[count_view] = await _count(conn, rfq_view_sql(count_view))
            params = list(search_params)
            clauses = [search_sql] if search_sql else []
            if view_sql:
                clauses.append(view_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            items = await conn.fetch(
                f"""
                {RFQ_LIST_SQL}
                {where}
                ORDER BY r.updated_at DESC, r.created_at DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            buyers = await load_owners(conn, [item["actor_id"] for item in items])

        return {
            "items": [
                _serialize_rfq_list_item(item, buyers.get(item["actor_id"]))
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_rfq_detail(self, rfq_id: str) -> dict:
        async with self.connection() as conn:
            rfq = await conn.fetchrow(f"{RFQ_LIST_SQL} WHERE r.id = $1", rfq_id)
            if not rfq:
                raise NotFoundError("RFQ not found")
            detail = await conn.fetchrow(
                """
                SELECT description, updated_at, type::text AS type, category_id,
                       budget_type::text AS budget_type, budget_from, budget_to,
                       currency, deadline, visibility::text AS visibility, quantity,
                       delivery_country, delivery_city, delivery_address, delivery_date,
                       project_duration, start_date, team_size_required, experience_required
                FROM rfqs WHERE id = $1
                """,
                rfq_id,
            )
            attachments = await conn.fetch(
                """
                SELECT id, file_name, file_url, file_type
                FROM rfq_attachments WHERE rfq_id = $1
                """,
                rfq_id,
            )
            proposals = await conn.fetch(
                """
                SELECT p.id, p.supplier_actor_id, p.price, p.currency::text AS currency,
                       p.delivery_time, p.message, p.status::text AS status, p.created_at,
                       EXISTS (
                           SELECT 1 FROM proposal_attachments pa WHERE pa.proposal_id = p.id
                       ) AS has_attachment
                FROM proposals p
                WHERE p.rfq_id = $1
                ORDER BY p.created_at DESC
                """,
                rfq_id,
            )
            reports = await conn.fetch(
                """
                SELECT rr.id, rr.reason::text AS reason, rr.details, rr.status::text AS status,
                       rr.created_at, rr.resolved_at,
                       u.id AS reporter_id, u.email, u.first_name, u.last_name
                FROM rfq_reports rr
                JOIN users u ON u.id = rr.reporter_user_id
                WHERE rr.rfq_id = $1
                ORDER BY rr.created_at DESC
                """,
                rfq_id,
            )
            contract_messages = await conn.fetch(
                """
                SELECT m.id, m.sender_id, m.text, m.created_at,
                       c.id AS contract_id, c.proposal_id
                FROM contracts c
                JOIN conversations conv ON conv.contract_id = c.id
                JOIN messages m ON m.conversation_id = conv.id
                WHERE c.rfq_id = $1
                """,
                rfq_id,
            )
            buyers = await load_owners(conn, [rfq["actor_id"]])
            buyer = buyers.get(rfq["actor_id"])
            suppliers = await load_owners(
                conn, [proposal["supplier_actor_id"] for proposal in proposals]
            )
            history = await load_audit_history(conn, "rfq", rfq_id)

        messages = []
        for proposal in proposals:
            if proposal["message"]:
                supplier = suppliers.get(proposal["supplier_actor_id"])
                messages.append(
                    {
                        "id": f"proposal-{proposal['id']}",
                        "source": "proposal",
                        "proposal_id": proposal["id"],
                        "contract_id": None,
                        "sender_name": (
                            (supplier.get("company_title") or supplier.get("name"))
                            if supplier
                            else f"Supplier #{proposal['supplier_actor_id']}"
                        ),
                        "text": proposal["message"],
                        "created_at": proposal["created_at"],
                    }
                )
        for message in contract_messages:
            messages.append(
                {
                    "id": f"message-{message['id']}",
                    "source": "contract",
                    "proposal_id": message["proposal_id"],
                    "contract_id": message["contract_id"],
                    "sender_name": f"User #{message['sender_id']}",
                    "text": message["text"],
                    "created_at": message["created_at"],
                }
            )
        messages.sort(key=lambda item: item["created_at"], reverse=True)
        payload = _serialize_rfq_list_item(rfq, buyer)
        payload.update(
            {
                "description": detail["description"],
                "updated_at": detail["updated_at"],
                "requirements": {
                    "type": detail["type"],
                    "category_id": detail["category_id"],
                    "budget_type": detail["budget_type"],
                    "budget_from": detail["budget_from"],
                    "budget_to": detail["budget_to"],
                    "currency": detail["currency"],
                    "deadline": detail["deadline"],
                    "visibility": detail["visibility"],
                    "quantity": detail["quantity"],
                    "delivery_country": detail["delivery_country"],
                    "delivery_city": detail["delivery_city"],
                    "delivery_address": detail["delivery_address"],
                    "delivery_date": detail["delivery_date"],
                    "project_duration": detail["project_duration"],
                    "start_date": detail["start_date"],
                    "team_size_required": detail["team_size_required"],
                    "experience_required": detail["experience_required"],
                    "attachments": [
                        {
                            "id": row["id"],
                            "file_name": row["file_name"],
                            "file_url": file_url(row["file_url"]),
                            "file_type": row["file_type"],
                        }
                        for row in attachments
                    ],
                },
                "buyer": buyer,
                "proposals": [
                    {
                        "id": proposal["id"],
                        "supplier_actor_id": proposal["supplier_actor_id"],
                        "supplier": suppliers.get(proposal["supplier_actor_id"]),
                        "price": proposal["price"],
                        "currency": proposal["currency"],
                        "delivery_time": proposal["delivery_time"],
                        "message": proposal["message"],
                        "status": proposal["status"],
                        "created_at": proposal["created_at"],
                        "has_attachment": proposal["has_attachment"],
                    }
                    for proposal in proposals
                ],
                "messages": messages,
                "reports": [
                    {
                        "id": report["id"],
                        "reason": report["reason"],
                        "details": report["details"],
                        "status": report["status"],
                        "created_at": report["created_at"],
                        "resolved_at": report["resolved_at"],
                        "reporter": {
                            "id": report["reporter_id"],
                            "email": report["email"],
                            "name": f"{report['first_name']} {report['last_name']}".strip(),
                        },
                    }
                    for report in reports
                ],
                "history": history,
            }
        )
        return payload

    async def apply_rfq_action(
        self,
        rfq_id: str,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        allowed = {"hide", "close", "delete", "warn_buyer"}
        if action not in allowed:
            raise ValidationError("Unsupported RFQ action")
        ensure_permission(
            current_user,
            "admin_rfqs.delete" if action == "delete" else "admin_rfqs.update",
        )
        if action in {"hide", "close", "delete", "warn_buyer"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        async with self.connection() as conn:
            rfq = await conn.fetchrow(
                "SELECT id, title, actor_id, status::text AS status FROM rfqs WHERE id = $1",
                rfq_id,
            )
            if not rfq:
                raise NotFoundError("RFQ not found")
            previous_status = rfq["status"]
            terminal = {"completed", "cancelled", "expired", "archived"}

            if action == "delete" and rfq["status"] == "draft":
                await conn.execute("DELETE FROM rfq_attachments WHERE rfq_id = $1", rfq_id)
                await conn.execute("DELETE FROM rfq_invited_suppliers WHERE rfq_id = $1", rfq_id)
                await conn.execute("DELETE FROM rfq_reports WHERE rfq_id = $1", rfq_id)
                await conn.execute("DELETE FROM rfqs WHERE id = $1", rfq_id)
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.rfq.delete",
                    "rfq",
                    rfq_id,
                    {
                        "reason": reason.strip() if reason else None,
                        "previous_status": previous_status,
                        "status": "deleted",
                    },
                )
                return {"id": rfq_id, "action": action, "status": "deleted"}

            if action == "hide":
                status = "archived"
            elif action == "close":
                if rfq["status"] in terminal:
                    raise ConflictError("RFQ is already closed")
                status = "cancelled"
            elif action == "delete":
                status = "archived"
            else:
                status = rfq["status"]

            moderated = action in {"hide", "close", "delete"}
            await conn.execute(
                """
                UPDATE rfqs
                SET status = $1::rfq_status,
                    moderation_reason = CASE WHEN $3 THEN $4 ELSE moderation_reason END,
                    moderated_at = CASE WHEN $3 THEN NOW() ELSE moderated_at END,
                    updated_at = NOW()
                WHERE id = $2
                """,
                status,
                rfq_id,
                moderated,
                reason.strip() if reason else None,
            )
            if action in {"hide", "delete"}:
                await conn.execute(
                    """
                    UPDATE rfq_reports
                    SET status = 'resolved'::rfq_report_status,
                        resolved_at = NOW(),
                        resolved_by_id = $1
                    WHERE rfq_id = $2 AND status::text = 'open'
                    """,
                    actor_id_of(current_user),
                    rfq_id,
                )

            buyer_user_id = await resolve_owner_user_id(conn, rfq["actor_id"])
            if buyer_user_id:
                title, body = _rfq_action_notification(action, rfq["title"], reason)
                await notify_user(conn, buyer_user_id, title, body, href=f"/customer/rfqs/{rfq_id}")
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.rfq.{action}",
                "rfq",
                rfq_id,
                {
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": status,
                },
            )
        return {"id": rfq_id, "action": action, "status": status}

    async def list_proposals(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "all",
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _proposal_search_sql(normalized, search_params)

        async def _count(conn, extra_sql: str | None):
            clauses = [search_sql] if search_sql else []
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(await conn.fetchval(f"SELECT COUNT(*) FROM proposals p {where}", *search_params))

        async with self.connection() as conn:
            view_sql = proposal_view_sql(view)
            total = await _count(conn, view_sql)
            count_values = {}
            for count_view in ("all", "pending", "accepted", "rejected", "reported"):
                count_values[count_view] = await _count(conn, proposal_view_sql(count_view))
            params = list(search_params)
            clauses = [search_sql] if search_sql else []
            if view_sql:
                clauses.append(view_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            items = await conn.fetch(
                f"""
                {PROPOSAL_LIST_SQL}
                {where}
                ORDER BY p.created_at DESC, p.id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            actor_ids = [item["supplier_actor_id"] for item in items] + [
                item["buyer_actor_id"] for item in items if item["buyer_actor_id"]
            ]
            parties = await load_owners(conn, actor_ids)

        return {
            "items": [
                _serialize_proposal_list_item(
                    item,
                    parties.get(item["supplier_actor_id"]),
                    parties.get(item["buyer_actor_id"]) if item["buyer_actor_id"] else None,
                )
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_proposal_detail(self, proposal_id: int) -> dict:
        async with self.connection() as conn:
            proposal = await conn.fetchrow(
                f"{PROPOSAL_LIST_SQL} WHERE p.id = $1",
                proposal_id,
            )
            if not proposal:
                raise NotFoundError("Proposal not found")
            extra = await conn.fetchrow(
                """
                SELECT p.delivery_time, p.message,
                       pa.id AS attachment_id, pa.file_name, pa.file_url, pa.file_type,
                       rfq.id AS rfq_id, rfq.title AS rfq_title, rfq.status::text AS rfq_status,
                       c.id AS contract_id, c.title AS contract_title,
                       c.status::text AS contract_status, c.agreed_amount,
                       c.currency::text AS contract_currency, c.created_at AS contract_created_at
                FROM proposals p
                LEFT JOIN proposal_attachments pa ON pa.proposal_id = p.id
                LEFT JOIN rfqs rfq ON rfq.id = p.rfq_id
                LEFT JOIN contracts c ON c.proposal_id = p.id
                WHERE p.id = $1
                """,
                proposal_id,
            )
            reports = await conn.fetch(
                """
                SELECT pr.id, pr.reason::text AS reason, pr.details, pr.status::text AS status,
                       pr.created_at, pr.resolved_at,
                       u.id AS reporter_id, u.email, u.first_name, u.last_name
                FROM proposal_reports pr
                JOIN users u ON u.id = pr.reporter_user_id
                WHERE pr.proposal_id = $1
                ORDER BY pr.created_at DESC
                """,
                proposal_id,
            )
            contract_messages = []
            if extra["contract_id"]:
                contract_messages = await conn.fetch(
                    """
                    SELECT m.id, m.sender_id, m.text, m.created_at
                    FROM conversations conv
                    JOIN messages m ON m.conversation_id = conv.id
                    WHERE conv.contract_id = $1
                    """,
                    extra["contract_id"],
                )
            actor_ids = [proposal["supplier_actor_id"]]
            if proposal["buyer_actor_id"]:
                actor_ids.append(proposal["buyer_actor_id"])
            parties = await load_owners(conn, actor_ids)
            supplier = parties.get(proposal["supplier_actor_id"])
            buyer = parties.get(proposal["buyer_actor_id"]) if proposal["buyer_actor_id"] else None
            history = await load_audit_history(conn, "proposal", str(proposal_id))

        messages = []
        if extra["message"]:
            messages.append(
                {
                    "id": f"proposal-{proposal_id}",
                    "source": "proposal",
                    "text": extra["message"],
                    "sender_name": (
                        (supplier.get("company_title") or supplier.get("name"))
                        if supplier
                        else f"Supplier #{proposal['supplier_actor_id']}"
                    ),
                    "created_at": proposal["created_at"],
                }
            )
        for message in contract_messages:
            messages.append(
                {
                    "id": f"message-{message['id']}",
                    "source": "contract",
                    "text": message["text"],
                    "sender_name": f"User #{message['sender_id']}",
                    "created_at": message["created_at"],
                }
            )
        messages.sort(key=lambda item: item["created_at"], reverse=True)
        payload = _serialize_proposal_list_item(proposal, supplier, buyer)
        payload.update(
            {
                "delivery_time": extra["delivery_time"],
                "message": extra["message"],
                "attachment": (
                    {
                        "id": extra["attachment_id"],
                        "file_name": extra["file_name"],
                        "file_url": file_url(extra["file_url"]),
                        "file_type": extra["file_type"],
                    }
                    if extra["attachment_id"]
                    else None
                ),
                "supplier": supplier,
                "buyer": buyer,
                "rfq": (
                    {
                        "id": extra["rfq_id"],
                        "title": extra["rfq_title"],
                        "status": extra["rfq_status"],
                    }
                    if extra["rfq_id"]
                    else None
                ),
                "contract": (
                    {
                        "id": extra["contract_id"],
                        "title": extra["contract_title"],
                        "status": extra["contract_status"],
                        "agreed_amount": extra["agreed_amount"],
                        "currency": extra["contract_currency"],
                        "created_at": extra["contract_created_at"],
                    }
                    if extra["contract_id"]
                    else None
                ),
                "messages": messages,
                "reports": [
                    {
                        "id": report["id"],
                        "reason": report["reason"],
                        "details": report["details"],
                        "status": report["status"],
                        "created_at": report["created_at"],
                        "resolved_at": report["resolved_at"],
                        "reporter": {
                            "id": report["reporter_id"],
                            "email": report["email"],
                            "name": f"{report['first_name']} {report['last_name']}".strip(),
                        },
                    }
                    for report in reports
                ],
                "history": history,
            }
        )
        return payload

    async def apply_proposal_action(
        self,
        proposal_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        allowed = {"delete", "investigate", "block_supplier"}
        if action not in allowed:
            raise ValidationError("Unsupported proposal action")
        ensure_permission(
            current_user,
            "admin_proposals.delete"
            if action in {"delete", "block_supplier"}
            else "admin_proposals.update",
        )
        if action in {"delete", "investigate", "block_supplier"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        async with self.connection() as conn:
            proposal = await conn.fetchrow(
                """
                SELECT p.id, p.status::text AS status, p.supplier_actor_id,
                       EXISTS (SELECT 1 FROM contracts c WHERE c.proposal_id = p.id) AS has_contract
                FROM proposals p WHERE p.id = $1
                """,
                proposal_id,
            )
            if not proposal:
                raise NotFoundError("Proposal not found")
            previous_status = proposal["status"]
            status = previous_status
            blocked_company_id = None

            if action == "delete":
                if proposal["has_contract"]:
                    raise ConflictError("Cannot delete proposal with an existing contract")
                await conn.execute(
                    "DELETE FROM proposal_attachments WHERE proposal_id = $1",
                    proposal_id,
                )
                await conn.execute(
                    "DELETE FROM proposal_reports WHERE proposal_id = $1",
                    proposal_id,
                )
                await conn.execute("DELETE FROM proposals WHERE id = $1", proposal_id)
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.proposal.delete",
                    "proposal",
                    str(proposal_id),
                    {
                        "reason": reason.strip() if reason else None,
                        "previous_status": previous_status,
                        "status": "deleted",
                    },
                )
                return {"id": proposal_id, "action": action, "status": "deleted"}

            if action == "investigate":
                await conn.execute(
                    """
                    UPDATE proposal_reports
                    SET status = 'resolved'::proposal_report_status,
                        resolved_at = NOW(),
                        resolved_by_id = $1
                    WHERE proposal_id = $2 AND status::text = 'open'
                    """,
                    actor_id_of(current_user),
                    proposal_id,
                )
            elif action == "block_supplier":
                owners = await load_owners(conn, [proposal["supplier_actor_id"]])
                owner = owners.get(proposal["supplier_actor_id"])
                company_id = owner.get("company_id") if owner else None
                if not company_id:
                    raise ValidationError("Supplier has no company to block")
                blocked = await self.apply_company_action(
                    company_id=company_id,
                    action="block",
                    current_user=current_user,
                    reason=reason,
                )
                blocked_company_id = blocked["id"]

            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.proposal.{action}",
                "proposal",
                str(proposal["id"]),
                {
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": status,
                    "blocked_company_id": blocked_company_id,
                },
            )
        return {
            "id": proposal["id"],
            "action": action,
            "status": status,
            "blocked_company_id": blocked_company_id,
        }
