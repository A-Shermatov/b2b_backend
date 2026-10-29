from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.modules.admin.services.common import (
    actor_id_of,
    load_audit_history,
    log_admin,
    normalize_query,
    notify_user,
    pages_for,
    with_reason,
)
from app.modules.verification.common import (
    DOCUMENT_TYPES,
    INDIVIDUAL_STATUS_LABELS,
    INDIVIDUAL_STATUSES,
    QUEUE_STATUSES,
    load_documents,
    load_individual_sides,
    load_verification_state,
    sync_individual_trust_level,
)

INDIVIDUAL_ACTIONS = {
    "approve": "verified",
    "reject": "rejected",
    "request_documents": "needs_documents",
}
LIST_STATUSES = ("queue", "all") + INDIVIDUAL_STATUSES

_STAFF_ROLES_SQL = ", ".join(f"'{role}'" for role in sorted(STAFF_PLATFORM_ROLES))

_NOTIFICATION_TEXT = {
    "verified": ("Личность подтверждена", "Ваша личность подтверждена. Профиль отмечен как проверенный."),
    "rejected": ("Проверка личности отклонена", "Проверка личности отклонена."),
    "needs_documents": (
        "Нужны дополнительные документы",
        "Для подтверждения личности нужны дополнительные документы.",
    ),
    "pending": ("Проверка личности", "Ваша заявка на проверку личности снова на рассмотрении."),
    "none": ("Статус проверки сброшен", "Статус проверки личности сброшен администратором."),
}


def _individual_filters(side: str | None, query: str | None, params: list) -> list[str]:
    side_sql = ""
    if side:
        params.append(side)
        side_sql = f" AND ia.side::text = ${len(params)}"
    clauses = [
        f"u.role::text NOT IN ({_STAFF_ROLES_SQL})",
        f"""EXISTS (
            SELECT 1 FROM actors ia
            WHERE ia.user_id = u.id AND ia.kind::text = 'individual'{side_sql}
        )""",
    ]
    normalized = normalize_query(query)
    if normalized:
        params.append(f"%{normalized}%")
        idx = len(params)
        search = (
            f"u.email ILIKE ${idx} OR u.first_name ILIKE ${idx} OR u.last_name ILIKE ${idx}"
            f" OR u.phone ILIKE ${idx}"
        )
        if normalized.isdigit():
            params.append(int(normalized))
            search += f" OR u.id = ${len(params)}"
        clauses.append(f"({search})")
    return clauses


def _status_clause(status: str) -> str | None:
    if status == "queue":
        return "u.individual_verification_status IN ('pending', 'needs_documents')"
    if status in INDIVIDUAL_STATUSES:
        return f"u.individual_verification_status = '{status}'"
    return None


def _profile_href(sides: list[str]) -> str:
    return "/supplier/profile" if "supplier" in sides else "/customer/profile"


class AdminIndividualsMixin:
    async def list_individuals(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str = "queue",
        side: str | None = None,
        query: str | None = None,
    ) -> dict:
        if status not in LIST_STATUSES:
            raise ValidationError("Неизвестный статус")
        params: list = []
        clauses = _individual_filters(side, query, params)
        base_where = " AND ".join(clauses)
        status_sql = _status_clause(status)
        where = f"{base_where} AND {status_sql}" if status_sql else base_where
        order = (
            "u.individual_verification_submitted_at ASC NULLS LAST, u.id ASC"
            if status in QUEUE_STATUSES + ("queue",)
            else "u.individual_verification_submitted_at DESC NULLS LAST, u.id DESC"
        )
        async with self.connection() as conn:
            counts = await conn.fetchrow(
                f"""
                SELECT
                    COUNT(*) AS all_count,
                    COUNT(*) FILTER (
                        WHERE u.individual_verification_status IN ('pending', 'needs_documents')
                    ) AS queue_count,
                    COUNT(*) FILTER (WHERE u.individual_verification_status = 'pending') AS pending,
                    COUNT(*) FILTER (
                        WHERE u.individual_verification_status = 'needs_documents'
                    ) AS needs_documents,
                    COUNT(*) FILTER (WHERE u.individual_verification_status = 'verified') AS verified,
                    COUNT(*) FILTER (WHERE u.individual_verification_status = 'rejected') AS rejected,
                    COUNT(*) FILTER (WHERE u.individual_verification_status = 'none') AS none
                FROM users u
                WHERE {base_where}
                """,
                *params,
            )
            total = int(
                await conn.fetchval(f"SELECT COUNT(*) FROM users u WHERE {where}", *params) or 0
            )
            rows = await conn.fetch(
                f"""
                SELECT
                    u.id, u.email, u.first_name, u.last_name, u.phone,
                    u.status::text AS account_status, u.created_at,
                    u.individual_verification_status AS verification_status,
                    u.individual_verification_reason AS verification_reason,
                    u.individual_verification_submitted_at AS submitted_at,
                    u.individual_verified_at AS verified_at,
                    COALESCE((
                        SELECT array_agg(DISTINCT a.side::text ORDER BY a.side::text)
                        FROM actors a
                        WHERE a.user_id = u.id AND a.kind::text = 'individual' AND a.is_active
                    ), '{{}}'::text[]) AS sides,
                    (SELECT COUNT(*) FROM user_documents d WHERE d.user_id = u.id) AS documents_count
                FROM users u
                WHERE {where}
                ORDER BY {order}
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                page_size,
                (page - 1) * page_size,
            )
        items = [
            {
                "id": row["id"],
                "email": row["email"],
                "name": f"{row['first_name']} {row['last_name']}".strip(),
                "phone": row["phone"],
                "account_status": row["account_status"],
                "verification_status": row["verification_status"],
                "verification_reason": row["verification_reason"],
                "submitted_at": row["submitted_at"],
                "verified_at": row["verified_at"],
                "created_at": row["created_at"],
                "sides": list(row["sides"] or []),
                "documents_count": int(row["documents_count"] or 0),
            }
            for row in rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size),
            "status_counts": {
                "all": int(counts["all_count"] or 0),
                "queue": int(counts["queue_count"] or 0),
                "pending": int(counts["pending"] or 0),
                "needs_documents": int(counts["needs_documents"] or 0),
                "verified": int(counts["verified"] or 0),
                "rejected": int(counts["rejected"] or 0),
                "none": int(counts["none"] or 0),
            },
        }

    async def _load_individual_user(self, conn, user_id: int, *, lock: bool = False):
        state = await load_verification_state(conn, user_id, lock=lock)
        if not state:
            raise NotFoundError("Пользователь не найден")
        if state["role"] in STAFF_PLATFORM_ROLES:
            raise ValidationError("Сотрудники платформы не проходят проверку личности")
        return state

    async def get_individual(self, user_id: int) -> dict:
        async with self.connection() as conn:
            state = await self._load_individual_user(conn, user_id)
            user = await conn.fetchrow(
                """
                SELECT u.phone, u.status::text AS account_status, u.created_at,
                       vb.email AS verified_by_email,
                       TRIM(COALESCE(vb.first_name, '') || ' ' || COALESCE(vb.last_name, ''))
                           AS verified_by_name
                FROM users u
                LEFT JOIN users vb ON vb.id = u.individual_verified_by
                WHERE u.id = $1
                """,
                user_id,
            )
            documents = await load_documents(conn, user_id)
            sides = await load_individual_sides(conn, user_id)
            companies_count = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM company_users WHERE user_id = $1", user_id
                )
                or 0
            )
            history = [
                entry
                for entry in await load_audit_history(conn, "user", str(user_id), limit=100)
                if entry["action"].startswith(("admin.individual.", "verification.individual."))
            ][:30]
        status = state["status"] or "none"
        return {
            "id": state["id"],
            "email": state["email"],
            "name": f"{state['first_name']} {state['last_name']}".strip(),
            "phone": user["phone"],
            "role": state["role"],
            "account_status": user["account_status"],
            "created_at": user["created_at"],
            "verification_status": status,
            "verification_status_label": INDIVIDUAL_STATUS_LABELS.get(status, status),
            "verification_reason": state["reason"],
            "submitted_at": state["submitted_at"],
            "verified_at": state["verified_at"],
            "verified_by": (
                {"email": user["verified_by_email"], "name": user["verified_by_name"] or None}
                if user["verified_by_email"]
                else None
            ),
            "sides": sides,
            "companies_count": companies_count,
            "documents": documents,
            "history": history,
        }

    async def set_individual_verification_admin(
        self,
        user_id: int,
        action: str,
        current_user,
        *,
        status: str | None = None,
        reason: str | None = None,
    ) -> dict:
        if action == "set":
            if status not in INDIVIDUAL_STATUSES:
                raise ValidationError("Укажите статус проверки")
            next_status = status
        elif action in INDIVIDUAL_ACTIONS:
            next_status = INDIVIDUAL_ACTIONS[action]
        else:
            raise ValidationError("Неизвестное действие")
        cleaned_reason = reason.strip() if reason and reason.strip() else None
        if next_status != "verified" and not cleaned_reason:
            raise ValidationError("Укажите причину решения")

        async with self.connection() as conn:
            async with conn.transaction():
                state = await self._load_individual_user(conn, user_id, lock=True)
                previous = state["status"] or "none"
                verified = next_status == "verified"
                await conn.execute(
                    """
                    UPDATE users
                    SET individual_verification_status = $2::varchar,
                        individual_verification_reason = $3::text,
                        individual_verified_at = CASE
                            WHEN $4::boolean AND individual_verification_status = 'verified'
                                THEN individual_verified_at
                            WHEN $4::boolean THEN NOW()
                        END,
                        individual_verified_by = CASE WHEN $4::boolean THEN $5::int END,
                        individual_verification_submitted_at = CASE
                            WHEN $2::varchar = 'none' THEN NULL
                            ELSE COALESCE(individual_verification_submitted_at, NOW())
                        END,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    user_id,
                    next_status,
                    None if verified else cleaned_reason,
                    verified,
                    actor_id_of(current_user),
                )
                if verified:
                    await conn.execute(
                        """
                        UPDATE user_documents SET status = 'accepted'
                        WHERE user_id = $1 AND status = 'uploaded'
                        """,
                        user_id,
                    )
                await sync_individual_trust_level(conn, user_id, next_status)
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.individual.verification.set",
                    "user",
                    user_id,
                    {
                        "action": action,
                        "changes": {
                            "individual_verification_status": {
                                "before": previous,
                                "after": next_status,
                            }
                        },
                        "previous_status": previous,
                        "status": next_status,
                        "revoked": previous == "verified" and not verified,
                        "reason": cleaned_reason,
                    },
                )
                if previous != next_status or cleaned_reason:
                    title, body = _NOTIFICATION_TEXT[next_status]
                    await notify_user(
                        conn,
                        user_id,
                        title,
                        with_reason(body, cleaned_reason),
                        href=_profile_href(await load_individual_sides(conn, user_id)),
                    )
        return await self.get_individual(user_id)

    async def add_individual_document_admin(
        self,
        user_id: int,
        current_user,
        *,
        doc_type: str,
        file_name: str | None,
        file_key: str,
    ) -> dict:
        if doc_type not in DOCUMENT_TYPES:
            raise ValidationError("Неизвестный тип документа")
        async with self.connection() as conn:
            async with conn.transaction():
                await self._load_individual_user(conn, user_id, lock=True)
                document_id = await conn.fetchval(
                    """
                    INSERT INTO user_documents (user_id, doc_type, file_name, file_url, uploaded_by)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                    """,
                    user_id,
                    doc_type,
                    (file_name or "")[:255] or None,
                    file_key,
                    actor_id_of(current_user),
                )
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.individual.document.add",
                    "user",
                    user_id,
                    {
                        "document_id": document_id,
                        "doc_type": doc_type,
                        "file_name": file_name,
                        "file_url": file_key,
                    },
                )
        return await self.get_individual(user_id)

    async def delete_individual_document_admin(
        self, user_id: int, document_id: int, current_user
    ) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                await self._load_individual_user(conn, user_id, lock=True)
                document = await conn.fetchrow(
                    """
                    DELETE FROM user_documents WHERE id = $1 AND user_id = $2
                    RETURNING id, doc_type, file_name, file_url, status
                    """,
                    document_id,
                    user_id,
                )
                if not document:
                    raise NotFoundError("Документ не найден")
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.individual.document.delete",
                    "user",
                    user_id,
                    {"document_id": document_id, "before": dict(document)},
                )
        return await self.get_individual(user_id)

    async def get_verification_counts(self) -> dict:
        side_company_sql = """
            SELECT COUNT(*) FROM companies c
            WHERE c.verification_status::text IN ('pending', 'needs_documents')
              AND (
                c.actor_type::text = $1
                OR EXISTS (
                    SELECT 1 FROM actors fa
                    WHERE fa.company_id = c.id AND fa.kind::text = 'company'
                      AND fa.is_active = TRUE AND fa.side::text = $1
                )
              )
        """
        async with self.connection() as conn:
            supplier_companies = int(await conn.fetchval(side_company_sql, "supplier") or 0)
            buyer_companies = int(await conn.fetchval(side_company_sql, "buyer") or 0)
            individuals = int(
                await conn.fetchval(
                    f"""
                    SELECT COUNT(*) FROM users u
                    WHERE u.individual_verification_status IN ('pending', 'needs_documents')
                      AND u.role::text NOT IN ({_STAFF_ROLES_SQL})
                    """
                )
                or 0
            )
            changes = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM company_change_requests WHERE status = 'pending'"
                )
                or 0
            )
        return {
            "supplier_companies": supplier_companies,
            "buyer_companies": buyer_companies,
            "individuals": individuals,
            "changes": changes,
        }
