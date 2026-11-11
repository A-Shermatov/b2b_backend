"""Changes to a public profile wait for an admin before anyone sees them.

A company's profile (requisites, description, pictures, facts, categories) and
a person's name, photo and personal page all go through one queue: the owner
proposes, an admin approves or rejects with a reason. While a request waits,
further edits are merged into it, so there is one request per profile.
"""

import json
from datetime import datetime, timezone

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import build_page
from app.modules.companies.services.common import ensure_can_manage
from app.modules.notifications.services import NotificationService
from app.utils.audit import AuditLogger, log_event
from app.utils.storage import public_file_url

# Requisites: never cleared, only replaced.
MODERATED_FIELDS = ("title", "legal_name", "tax_number", "address", "city", "country")
# Columns of `companies` an owner may change.
COMPANY_COLUMNS = MODERATED_FIELDS + ("website", "description", "logo", "cover")
# Columns of `company_profiles`.
COMPANY_PROFILE_COLUMNS = (
    "founded_year",
    "employees_count",
    "annual_revenue_range",
    "languages",
    "industries",
    "warehouse_area",
    "markets",
    "brands",
    "notable_clients",
    "gallery",
)
COMPANY_REVIEWED_FIELDS = COMPANY_COLUMNS + COMPANY_PROFILE_COLUMNS + ("category_ids",)
# A person: the name and photo others see, and the personal page.
USER_REVIEWED_FIELDS = ("first_name", "last_name", "avatar_url", "page")
IMAGE_FIELDS = ("logo", "cover", "avatar_url")

FIELD_LABELS = {
    "title": "Название",
    "legal_name": "Юридическое название",
    "tax_number": "ИНН",
    "address": "Адрес",
    "city": "Город",
    "country": "Страна",
    "website": "Сайт",
    "description": "Описание",
    "logo": "Логотип",
    "cover": "Обложка",
    "founded_year": "Год основания",
    "employees_count": "Сотрудников",
    "annual_revenue_range": "Оборот",
    "languages": "Языки",
    "industries": "Отрасли",
    "warehouse_area": "Склад",
    "markets": "Рынки",
    "brands": "Бренды",
    "notable_clients": "Клиенты",
    "gallery": "Фото компании",
    "category_ids": "Категории",
    "first_name": "Имя",
    "last_name": "Фамилия",
    "avatar_url": "Фото профиля",
    "page": "Личная страница",
}

REQUEST_SELECT = """
    SELECT ccr.*,
           COALESCE(
               c.title,
               NULLIF(TRIM(CONCAT(tu.first_name, ' ', tu.last_name)), ''),
               tu.email
           ) AS company_title
    FROM company_change_requests ccr
    LEFT JOIN companies c ON c.id = ccr.company_id
    LEFT JOIN users tu ON tu.id = ccr.target_user_id
"""


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _same(a, b) -> bool:
    """Equal as a person would see it: blank is blank, lists by content."""

    def norm(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    return norm(a) == norm(b)


def _media_preview(field: str, value):
    """What the admin looks at: a link for a picture, the pictures of a gallery."""
    if field in IMAGE_FIELDS:
        return public_file_url(value) if value else None
    if field == "gallery" and isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                src = item.get("src") or item.get("url") or item.get("file_url")
                if src:
                    out.append(public_file_url(src))
        return out
    if field == "page" and isinstance(value, dict):
        return [
            public_file_url(item["image"])
            for item in value.get("portfolio") or []
            if isinstance(item, dict) and item.get("image")
        ]
    return None


def _shape_change_request(row, *, company_title: str | None = None) -> dict:
    proposed = _json(row["proposed"]) or {}
    snapshot = _json(row["current_snapshot"]) or {}
    target_user_id = row.get("target_user_id")
    media = {}
    for field in proposed:
        after = _media_preview(field, proposed.get(field))
        before = _media_preview(field, snapshot.get(field))
        if after is not None or before is not None:
            media[field] = {"before": before, "after": after}
    return {
        "id": row["id"],
        "kind": "user" if target_user_id else "company",
        "company_id": row["company_id"],
        "target_user_id": target_user_id,
        "company_title": company_title or row.get("company_title"),
        "requested_by_user_id": row["requested_by_user_id"],
        "status": row["status"],
        "proposed": proposed,
        "current_snapshot": snapshot,
        "media": media,
        "reason_from_user": row["reason_from_user"],
        "admin_reason": row["admin_reason"],
        "reviewed_by_user_id": row["reviewed_by_user_id"],
        "reviewed_at": row["reviewed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "field_labels": {k: FIELD_LABELS.get(k, k) for k in proposed},
    }


async def company_snapshot(conn, company_id: int) -> dict:
    company = await conn.fetchrow(
        f"SELECT {', '.join(COMPANY_COLUMNS)} FROM companies WHERE id = $1",
        company_id,
    )
    if not company:
        raise NotFoundError("Company not found")
    snapshot = dict(company)
    profile = await conn.fetchrow(
        f"SELECT {', '.join(COMPANY_PROFILE_COLUMNS)} FROM company_profiles WHERE company_id = $1",
        company_id,
    )
    for field in COMPANY_PROFILE_COLUMNS:
        value = profile[field] if profile else None
        if field == "gallery":
            value = _json(value) or []
        elif field in ("languages", "industries"):
            value = list(value or [])
        snapshot[field] = value
    snapshot["category_ids"] = [
        row["category_id"]
        for row in await conn.fetch(
            "SELECT category_id FROM company_categories WHERE company_id = $1 ORDER BY category_id",
            company_id,
        )
    ]
    return snapshot


async def user_snapshot(conn, user_id: int) -> dict:
    user = await conn.fetchrow(
        "SELECT first_name, last_name, avatar_url FROM users WHERE id = $1", user_id
    )
    if not user:
        raise NotFoundError("User not found")
    page = await conn.fetchrow(
        """
        SELECT headline, about, skills, city, website, rate_amount,
               rate_currency, rate_unit, portfolio
        FROM individual_profiles WHERE user_id = $1
        """,
        user_id,
    )
    page_value = None
    if page:
        page_value = dict(page)
        page_value["skills"] = list(page_value.get("skills") or [])
        page_value["portfolio"] = _json(page_value.get("portfolio")) or []
        if page_value.get("rate_amount") is not None:
            page_value["rate_amount"] = float(page_value["rate_amount"])
    return {**dict(user), "page": page_value}


async def _queue(
    conn,
    *,
    company_id: int | None,
    target_user_id: int | None,
    requested_by: int,
    proposed: dict,
    snapshot: dict,
    reason: str | None,
) -> dict | None:
    """Add the changes to the profile's waiting request (or start one).

    Returns the request, or None when nothing differs from what is shown now.
    """
    key_column = "company_id" if company_id is not None else "target_user_id"
    key_value = company_id if company_id is not None else target_user_id
    pending = await conn.fetchrow(
        f"""
        SELECT * FROM company_change_requests
        WHERE {key_column} = $1 AND status = 'pending'
        FOR UPDATE
        """,
        key_value,
    )
    if pending:
        merged = {**(_json(pending["proposed"]) or {}), **proposed}
        base = {**snapshot, **(_json(pending["current_snapshot"]) or {})}
    else:
        merged = dict(proposed)
        base = snapshot
    # A field set back to what is shown now no longer needs a review.
    merged = {k: v for k, v in merged.items() if not _same(v, snapshot.get(k))}
    before = {k: base.get(k) for k in merged}

    if pending and not merged:
        await conn.execute(
            """
            UPDATE company_change_requests
            SET status = 'withdrawn', updated_at = NOW()
            WHERE id = $1
            """,
            pending["id"],
        )
        return None
    if not merged:
        return None

    payload = json.dumps(merged, ensure_ascii=False, default=str)
    before_payload = json.dumps(before, ensure_ascii=False, default=str)
    note = (reason or "").strip() or None
    if pending:
        row = await conn.fetchrow(
            """
            UPDATE company_change_requests
            SET proposed = $2::jsonb, current_snapshot = $3::jsonb,
                requested_by_user_id = $4,
                reason_from_user = COALESCE($5, reason_from_user),
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            pending["id"],
            payload,
            before_payload,
            requested_by,
            note,
        )
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO company_change_requests (
                company_id, target_user_id, requested_by_user_id, status,
                proposed, current_snapshot, reason_from_user
            )
            VALUES ($1, $2, $3, 'pending', $4::jsonb, $5::jsonb, $6)
            RETURNING *
            """,
            company_id,
            target_user_id,
            requested_by,
            payload,
            before_payload,
            note,
        )
    await log_event(
        conn,
        "company.change_request.submit" if company_id else "user.change_request.submit",
        "company" if company_id else "user",
        key_value,
        user_id=requested_by,
        company_id=company_id,
        details={"change_request_id": row["id"], "fields": sorted(proposed)},
    )
    return dict(row)


async def queue_company_changes(
    conn, company_id: int, user_id: int, proposed: dict, reason: str | None = None
) -> dict | None:
    """Changes to a company's profile, held for an admin. Call inside a transaction."""
    cleaned = {}
    for key, value in proposed.items():
        if key not in COMPANY_REVIEWED_FIELDS or value is None:
            continue
        if key in MODERATED_FIELDS and str(value).strip() == "":
            continue  # requisites are replaced, never cleared
        cleaned[key] = value.strip() if isinstance(value, str) else value
    if not cleaned:
        return None
    await conn.execute("SELECT 1 FROM companies WHERE id = $1 FOR UPDATE", company_id)
    snapshot = await company_snapshot(conn, company_id)
    return await _queue(
        conn,
        company_id=company_id,
        target_user_id=None,
        requested_by=user_id,
        proposed=cleaned,
        snapshot=snapshot,
        reason=reason,
    )


async def queue_user_changes(conn, user_id: int, proposed: dict) -> dict | None:
    """A person's name, photo or page, held for an admin. Call inside a transaction."""
    cleaned = {k: v for k, v in proposed.items() if k in USER_REVIEWED_FIELDS}
    if not cleaned:
        return None
    snapshot = await user_snapshot(conn, user_id)
    return await _queue(
        conn,
        company_id=None,
        target_user_id=user_id,
        requested_by=user_id,
        proposed=cleaned,
        snapshot=snapshot,
        reason=None,
    )


async def pending_user_change_request(conn, user_id: int) -> dict | None:
    row = await conn.fetchrow(
        f"{REQUEST_SELECT} WHERE ccr.target_user_id = $1 AND ccr.status = 'pending'",
        user_id,
    )
    return _shape_change_request(row) if row else None


async def _apply_company(conn, company_id: int, proposed: dict) -> None:
    sets, params = [], []
    for field in COMPANY_COLUMNS:
        if field in proposed:
            value = proposed[field]
            params.append(value if value != "" or field in MODERATED_FIELDS else None)
            sets.append(f"{field} = ${len(params)}")
    if sets:
        params.append(company_id)
        await conn.execute(
            f"UPDATE companies SET {', '.join(sets)}, updated_at = NOW() WHERE id = ${len(params)}",
            *params,
        )
    if "title" in proposed:
        await conn.execute(
            """
            UPDATE actors SET display_name = $1, updated_at = NOW()
            WHERE kind::text = 'company' AND company_id = $2
            """,
            proposed["title"],
            company_id,
        )

    sets, params = [], []
    for field in COMPANY_PROFILE_COLUMNS:
        if field not in proposed:
            continue
        value = proposed[field]
        if field == "gallery":
            params.append(json.dumps(value or [], ensure_ascii=False))
            sets.append(f"{field} = ${len(params)}::jsonb")
        elif field in ("languages", "industries"):
            params.append(list(value or []))
            sets.append(f"{field} = ${len(params)}::text[]")
        else:
            params.append(value if value != "" else None)
            sets.append(f"{field} = ${len(params)}")
    if sets:
        await conn.execute(
            """
            INSERT INTO company_profiles (company_id, languages, industries)
            VALUES ($1, '{}'::text[], '{}'::text[])
            ON CONFLICT (company_id) DO NOTHING
            """,
            company_id,
        )
        params.append(company_id)
        await conn.execute(
            f"UPDATE company_profiles SET {', '.join(sets)} WHERE company_id = ${len(params)}",
            *params,
        )

    if "category_ids" in proposed:
        await conn.execute("DELETE FROM company_categories WHERE company_id = $1", company_id)
        for category_id in proposed["category_ids"] or []:
            await conn.execute(
                """
                INSERT INTO company_categories (company_id, category_id)
                SELECT $1, $2 WHERE EXISTS (SELECT 1 FROM categories WHERE id = $2)
                ON CONFLICT DO NOTHING
                """,
                company_id,
                category_id,
            )


async def _apply_user(conn, user_id: int, proposed: dict) -> None:
    from app.modules.actors.services import individual_display_name

    sets, params = [], []
    for field in ("first_name", "last_name", "avatar_url"):
        if field in proposed:
            params.append(proposed[field])
            sets.append(f"{field} = ${len(params)}")
    if sets:
        params.append(user_id)
        user = await conn.fetchrow(
            f"""
            UPDATE users SET {', '.join(sets)}, updated_at = NOW()
            WHERE id = ${len(params)}
            RETURNING first_name, last_name, email
            """,
            *params,
        )
        if user and ("first_name" in proposed or "last_name" in proposed):
            await conn.execute(
                """
                UPDATE actors SET display_name = $1, updated_at = NOW()
                WHERE kind::text = 'individual' AND user_id = $2
                """,
                individual_display_name(dict(user)),
                user_id,
            )

    page = proposed.get("page")
    if isinstance(page, dict):
        await conn.execute(
            """
            INSERT INTO individual_profiles (
                user_id, headline, about, skills, city, website,
                rate_amount, rate_currency, rate_unit, portfolio
            )
            VALUES ($1, $2, $3, $4::text[], $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (user_id) DO UPDATE SET
                headline = EXCLUDED.headline,
                about = EXCLUDED.about,
                skills = EXCLUDED.skills,
                city = EXCLUDED.city,
                website = EXCLUDED.website,
                rate_amount = EXCLUDED.rate_amount,
                rate_currency = EXCLUDED.rate_currency,
                rate_unit = EXCLUDED.rate_unit,
                portfolio = EXCLUDED.portfolio,
                updated_at = NOW()
            """,
            user_id,
            page.get("headline"),
            page.get("about"),
            list(page.get("skills") or []),
            page.get("city"),
            page.get("website"),
            page.get("rate_amount"),
            page.get("rate_currency") or "TJS",
            page.get("rate_unit") or "project",
            json.dumps(page.get("portfolio") or [], ensure_ascii=False),
        )


class CompanyChangesMixin:
    async def submit_change_request(
        self,
        company_id: int,
        user_id: int,
        proposed: dict,
        reason: str | None = None,
    ) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                exists = await conn.fetchval("SELECT 1 FROM companies WHERE id = $1", company_id)
                if not exists:
                    raise NotFoundError("Company not found")
                await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
                if not any(
                    value is not None and str(value).strip() != ""
                    for key, value in proposed.items()
                    if key in COMPANY_REVIEWED_FIELDS
                ):
                    raise ValidationError("Укажите хотя бы одно поле для изменения")
                row = await queue_company_changes(conn, company_id, user_id, proposed, reason)
                if row is None:
                    raise ValidationError("Нет изменений относительно текущих данных")
                title = await conn.fetchval("SELECT title FROM companies WHERE id = $1", company_id)
                return _shape_change_request(row, company_title=title)

    async def list_company_change_requests(self, company_id: int, user_id: int) -> list[dict]:
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
            rows = await conn.fetch(
                f"""
                {REQUEST_SELECT}
                WHERE ccr.company_id = $1 AND ccr.status <> 'withdrawn'
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
                f"""
                {REQUEST_SELECT}
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
            n = len(params)
            filters.append(
                f"(c.title ILIKE ${n} OR c.legal_name ILIKE ${n} OR COALESCE(c.tax_number, '') ILIKE ${n}"
                f" OR CONCAT(tu.first_name, ' ', tu.last_name) ILIKE ${n} OR tu.email ILIKE ${n})"
            )
        where = " AND ".join(filters)
        offset = (page - 1) * page_size
        async with self.connection() as conn:
            total = int(
                await conn.fetchval(
                    f"""
                    SELECT COUNT(*)
                    FROM company_change_requests ccr
                    LEFT JOIN companies c ON c.id = ccr.company_id
                    LEFT JOIN users tu ON tu.id = ccr.target_user_id
                    WHERE {where}
                    """,
                    *params,
                )
                or 0
            )
            rows = await conn.fetch(
                f"""
                {REQUEST_SELECT}
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
            row = await conn.fetchrow(f"{REQUEST_SELECT} WHERE ccr.id = $1", request_id)
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
                    f"""
                    SELECT ccr.*, c.title AS company_title, c.owner_id,
                           c.actor_type::text AS actor_type,
                           tu.role::text AS target_role
                    FROM company_change_requests ccr
                    LEFT JOIN companies c ON c.id = ccr.company_id
                    LEFT JOIN users tu ON tu.id = ccr.target_user_id
                    WHERE ccr.id = $1
                    FOR UPDATE OF ccr
                    """,
                    request_id,
                )
                if not row:
                    raise NotFoundError("Change request not found")
                if row["status"] != "pending":
                    raise ConflictError("Заявка уже обработана")

                proposed = _json(row["proposed"]) or {}
                row_snapshot = _json(row["current_snapshot"]) or {}
                is_user = row["target_user_id"] is not None
                now = datetime.now(timezone.utc)
                if action == "approve":
                    if is_user:
                        await _apply_user(conn, row["target_user_id"], proposed)
                    else:
                        await _apply_company(conn, row["company_id"], proposed)
                    await conn.execute(
                        """
                        UPDATE company_change_requests
                        SET status = 'approved', reviewed_by_user_id = $2, reviewed_at = $3,
                            admin_reason = NULL, updated_at = NOW()
                        WHERE id = $1
                        """,
                        request_id,
                        current_user.id,
                        now,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE company_change_requests
                        SET status = 'rejected', reviewed_by_user_id = $2, reviewed_at = $3,
                            admin_reason = $4, updated_at = NOW()
                        WHERE id = $1
                        """,
                        request_id,
                        current_user.id,
                        now,
                        reason.strip(),
                    )

                fields = ", ".join(FIELD_LABELS.get(k, k).lower() for k in proposed)
                if is_user:
                    title = "Изменения профиля одобрены" if action == "approve" else "Изменения профиля отклонены"
                    body = (
                        f"Администратор проверил изменения профиля ({fields})."
                        if action == "approve"
                        else f"Изменения профиля ({fields}) не приняты. Причина: {reason.strip()}"
                    )
                    side = "supplier" if row["target_role"] == "supplier" else "customer"
                    recipient, href = row["target_user_id"], f"/{side}/profile"
                else:
                    title = "Изменения данных одобрены" if action == "approve" else "Изменения данных отклонены"
                    body = (
                        f"Изменения профиля компании «{row['company_title']}» ({fields}) опубликованы."
                        if action == "approve"
                        else (
                            f"Изменения профиля компании «{row['company_title']}» ({fields}) не приняты. "
                            f"Причина: {reason.strip()}"
                        )
                    )
                    side = "supplier" if row["actor_type"] == "supplier" else "customer"
                    recipient = row["requested_by_user_id"] or row["owner_id"]
                    href = f"/{side}/company/{row['company_id']}"

                await AuditLogger(conn).log_action(
                    user_id=current_user.id,
                    actor_id=None,
                    company_id=None,
                    action=(
                        f"admin.user.change_request.{action}"
                        if is_user
                        else f"admin.company.change_request.{action}"
                    ),
                    resource_type="user" if is_user else "company",
                    resource_id=str(row["target_user_id"] if is_user else row["company_id"]),
                    details={
                        "request_id": request_id,
                        "changes": {
                            field: {"before": row_snapshot.get(field), "after": value}
                            for field, value in proposed.items()
                        },
                        "applied": action == "approve",
                        "reason": reason.strip() if reason and reason.strip() else None,
                    },
                )
                await NotificationService(conn).notify_user(
                    recipient, "system", title, body, href=href
                )

                updated = await conn.fetchrow(f"{REQUEST_SELECT} WHERE ccr.id = $1", request_id)
                return _shape_change_request(updated)
