import json
from datetime import datetime, timezone

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import build_page
from app.modules.companies.services.common import ensure_can_manage
from app.modules.notifications.services import NotificationService
from app.utils.audit import AuditLogger, log_event

MODERATED_FIELDS = ("title", "legal_name", "tax_number", "address", "city", "country")

FIELD_LABELS = {
    "title": "Название",
    "legal_name": "Юридическое название",
    "tax_number": "ИНН",
    "address": "Адрес",
    "city": "Город",
    "country": "Страна",
}


def _shape_change_request(row, *, company_title: str | None = None) -> dict:
    proposed = row["proposed"]
    snapshot = row["current_snapshot"]
    if isinstance(proposed, str):
        proposed = json.loads(proposed)
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "company_title": company_title or row.get("company_title"),
        "requested_by_user_id": row["requested_by_user_id"],
        "status": row["status"],
        "proposed": proposed or {},
        "current_snapshot": snapshot or {},
        "reason_from_user": row["reason_from_user"],
        "admin_reason": row["admin_reason"],
        "reviewed_by_user_id": row["reviewed_by_user_id"],
        "reviewed_at": row["reviewed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "field_labels": {k: FIELD_LABELS.get(k, k) for k in (proposed or {})},
    }


class CompanyChangesMixin:
    async def submit_change_request(
        self,
        company_id: int,
        user_id: int,
        proposed: dict,
        reason: str | None = None,
    ) -> dict:
        cleaned = {
            key: value
            for key, value in proposed.items()
            if key in MODERATED_FIELDS and value is not None and str(value).strip() != ""
        }
        if not cleaned:
            raise ValidationError("Укажите хотя бы одно поле для изменения")

        async with self.connection() as conn:
            async with conn.transaction():
                company = await conn.fetchrow(
                    """
                    SELECT id, title, legal_name, tax_number, address, city, country,
                           verification_status::text AS verification_status, owner_id
                    FROM companies WHERE id = $1
                    FOR UPDATE
                    """,
                    company_id,
                )
                if not company:
                    raise NotFoundError("Company not found")
                await ensure_can_manage(conn, company_id, user_id, "company_profile.update")

                snapshot = {key: company[key] for key in MODERATED_FIELDS}
                changed = {
                    key: cleaned[key]
                    for key in cleaned
                    if str(cleaned[key]).strip() != str(snapshot.get(key) or "").strip()
                }
                if not changed:
                    raise ValidationError("Нет изменений относительно текущих данных")

                existing = await conn.fetchval(
                    """
                    SELECT id FROM company_change_requests
                    WHERE company_id = $1 AND status = 'pending'
                    """,
                    company_id,
                )
                if existing:
                    raise ConflictError("У компании уже есть заявка на изменение данных")

                row = await conn.fetchrow(
                    """
                    INSERT INTO company_change_requests (
                        company_id, requested_by_user_id, status,
                        proposed, current_snapshot, reason_from_user
                    )
                    VALUES ($1, $2, 'pending', $3::jsonb, $4::jsonb, $5)
                    RETURNING *
                    """,
                    company_id,
                    user_id,
                    json.dumps(changed, ensure_ascii=False),
                    json.dumps(snapshot, ensure_ascii=False),
                    (reason or "").strip() or None,
                )
                await log_event(
                    conn,
                    "company.change_request.submit",
                    "company",
                    company_id,
                    user_id=user_id,
                    company_id=company_id,
                    details={
                        "change_request_id": row["id"],
                        "before": {key: snapshot.get(key) for key in changed},
                        "after": changed,
                    },
                )
                return _shape_change_request(row, company_title=company["title"])

    async def list_company_change_requests(self, company_id: int, user_id: int) -> list[dict]:
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
            rows = await conn.fetch(
                """
                SELECT ccr.*, c.title AS company_title
                FROM company_change_requests ccr
                JOIN companies c ON c.id = ccr.company_id
                WHERE ccr.company_id = $1
                ORDER BY ccr.created_at DESC
                LIMIT 20
                """,
                company_id,
            )
            return [_shape_change_request(row) for row in rows]

    async def get_pending_change_request(self, company_id: int, user_id: int) -> dict | None:
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
            row = await conn.fetchrow(
                """
                SELECT ccr.*, c.title AS company_title
                FROM company_change_requests ccr
                JOIN companies c ON c.id = ccr.company_id
                WHERE ccr.company_id = $1 AND ccr.status = 'pending'
                ORDER BY ccr.created_at DESC
                LIMIT 1
                """,
                company_id,
            )
            return _shape_change_request(row) if row else None

    async def admin_list_change_requests(
        self, *, status: str = "pending", page: int = 1, page_size: int = 20, query: str | None = None
    ) -> dict:
        filters = ["ccr.status = $1"]
        params: list = [status]
        if query and query.strip():
            params.append(f"%{query.strip()}%")
            filters.append(
                f"(c.title ILIKE ${len(params)} OR c.legal_name ILIKE ${len(params)} OR COALESCE(c.tax_number, '') ILIKE ${len(params)})"
            )
        where = " AND ".join(filters)
        offset = (page - 1) * page_size
        async with self.connection() as conn:
            total = int(
                await conn.fetchval(
                    f"""
                    SELECT COUNT(*)
                    FROM company_change_requests ccr
                    JOIN companies c ON c.id = ccr.company_id
                    WHERE {where}
                    """,
                    *params,
                )
                or 0
            )
            rows = await conn.fetch(
                f"""
                SELECT ccr.*, c.title AS company_title
                FROM company_change_requests ccr
                JOIN companies c ON c.id = ccr.company_id
                WHERE {where}
                ORDER BY ccr.created_at ASC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                page_size,
                offset,
            )
            items = [_shape_change_request(row) for row in rows]
            return build_page(items, total, page, page_size)

    async def admin_get_change_request(self, request_id: int) -> dict:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT ccr.*, c.title AS company_title
                FROM company_change_requests ccr
                JOIN companies c ON c.id = ccr.company_id
                WHERE ccr.id = $1
                """,
                request_id,
            )
            if not row:
                raise NotFoundError("Change request not found")
            return _shape_change_request(row)

    async def admin_apply_change_request(
        self,
        request_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        if action not in {"approve", "reject"}:
            raise ValidationError("Unsupported action")
        if action == "reject" and not (reason and reason.strip()):
            raise ValidationError("Укажите причину отклонения")

        async with self.connection() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT ccr.*, c.title AS company_title, c.owner_id,
                           c.actor_type::text AS actor_type
                    FROM company_change_requests ccr
                    JOIN companies c ON c.id = ccr.company_id
                    WHERE ccr.id = $1
                    FOR UPDATE OF ccr
                    """,
                    request_id,
                )
                if not row:
                    raise NotFoundError("Change request not found")
                if row["status"] != "pending":
                    raise ConflictError("Заявка уже обработана")

                proposed = row["proposed"]
                if isinstance(proposed, str):
                    proposed = json.loads(proposed)
                proposed = proposed or {}
                row_snapshot = row["current_snapshot"]
                if isinstance(row_snapshot, str):
                    row_snapshot = json.loads(row_snapshot)

                now = datetime.now(timezone.utc)
                if action == "approve":
                    sets = []
                    params: list = []
                    for field in MODERATED_FIELDS:
                        if field in proposed:
                            params.append(proposed[field])
                            sets.append(f"{field} = ${len(params)}")
                    if sets:
                        params.append(row["company_id"])
                        await conn.execute(
                            f"""
                            UPDATE companies
                            SET {', '.join(sets)}, updated_at = NOW()
                            WHERE id = ${len(params)}
                            """,
                            *params,
                        )
                    if "title" in proposed:
                        await conn.execute(
                            """
                            UPDATE actors SET display_name = $1, updated_at = NOW()
                            WHERE kind::text = 'company' AND company_id = $2
                            """,
                            proposed["title"],
                            row["company_id"],
                        )
                    await conn.execute(
                        """
                        UPDATE company_change_requests
                        SET status = 'approved',
                            reviewed_by_user_id = $2,
                            reviewed_at = $3,
                            admin_reason = NULL,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        request_id,
                        current_user.id,
                        now,
                    )
                    title = "Изменения данных одобрены"
                    body = f"Заявка на изменение данных компании «{row['company_title']}» одобрена."
                else:
                    await conn.execute(
                        """
                        UPDATE company_change_requests
                        SET status = 'rejected',
                            reviewed_by_user_id = $2,
                            reviewed_at = $3,
                            admin_reason = $4,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        request_id,
                        current_user.id,
                        now,
                        reason.strip(),
                    )
                    title = "Изменения данных отклонены"
                    body = (
                        f"Заявка на изменение данных компании «{row['company_title']}» отклонена. "
                        f"Причина: {reason.strip()}"
                    )

                await AuditLogger(conn).log_action(
                    user_id=current_user.id,
                    actor_id=None,
                    company_id=None,
                    action=f"admin.company.change_request.{action}",
                    resource_type="company",
                    resource_id=str(row["company_id"]),
                    details={
                        "request_id": request_id,
                        "changes": {
                            field: {
                                "before": (row_snapshot or {}).get(field),
                                "after": value,
                            }
                            for field, value in proposed.items()
                        },
                        "applied": action == "approve",
                        "reason": reason.strip() if reason and reason.strip() else None,
                    },
                )

                href_side = "supplier" if row["actor_type"] == "supplier" else "customer"
                await NotificationService(conn).notify_user(
                    row["owner_id"],
                    "system",
                    title,
                    body,
                    href=f"/{href_side}/company/{row['company_id']}",
                )

                updated = await conn.fetchrow(
                    """
                    SELECT ccr.*, c.title AS company_title
                    FROM company_change_requests ccr
                    JOIN companies c ON c.id = ccr.company_id
                    WHERE ccr.id = $1
                    """,
                    request_id,
                )
                return _shape_change_request(updated)
