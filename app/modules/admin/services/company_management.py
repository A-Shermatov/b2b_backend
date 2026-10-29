from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.modules.admin.services.common import (
    actor_id_of,
    file_url,
    log_admin,
    notify_user,
    with_reason,
)
from app.modules.admin.services.companies import sync_company_trust_level
from app.modules.companies.services import CompanyService

COMPANY_FIELDS = (
    "title",
    "legal_name",
    "tax_number",
    "website",
    "description",
    "country",
    "city",
    "address",
    "logo",
)
PROFILE_FIELDS = (
    "founded_year",
    "employees_count",
    "annual_revenue_range",
    "languages",
    "industries",
)
PROFILE_ARRAY_FIELDS = {"languages", "industries"}
NULLABLE_COMPANY_FIELDS = set(COMPANY_FIELDS) - {"title"}

VERIFICATION_LABELS = {
    "verified": "Проверено",
    "pending": "На проверке",
    "needs_documents": "Нужны документы",
    "rejected": "Отклонено",
}


def _clean_text(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _company_href(company) -> str:
    side = "supplier" if company["actor_type"] == "supplier" else "customer"
    return f"/{side}/company/{company['id']}"


async def _load_company(conn, company_id: int, *, lock: bool = False):
    company = await conn.fetchrow(
        f"""
        SELECT id, title, owner_id, actor_type::text AS actor_type,
               legal_name, tax_number, website, description, country, city, address, logo,
               verification_status::text AS verification_status,
               operational_status::text AS operational_status
        FROM companies WHERE id = $1
        {"FOR UPDATE" if lock else ""}
        """,
        company_id,
    )
    if not company:
        raise NotFoundError("Company not found")
    return company


async def _find_marketplace_user(conn, *, user_id: int | None = None, email: str | None = None):
    if user_id is not None:
        user = await conn.fetchrow(
            """
            SELECT id, email, first_name, last_name, role::text AS role, status::text AS status
            FROM users WHERE id = $1
            """,
            user_id,
        )
    else:
        user = await conn.fetchrow(
            """
            SELECT id, email, first_name, last_name, role::text AS role, status::text AS status
            FROM users WHERE LOWER(email) = LOWER($1)
            """,
            (email or "").strip(),
        )
    if not user:
        raise NotFoundError("Пользователь не найден")
    if user["role"] in STAFF_PLATFORM_ROLES:
        raise ValidationError("Сотрудника платформы нельзя добавить в компанию")
    return user


async def _membership(conn, company_id: int, user_id: int):
    return await conn.fetchrow(
        """
        SELECT id, role::text AS role
        FROM company_users
        WHERE company_id = $1 AND user_id = $2
        """,
        company_id,
        user_id,
    )


class AdminCompanyManagementMixin:
    async def get_company_owner_id(self, company_id: int) -> int:
        async with self.connection() as conn:
            company = await _load_company(conn, company_id)
        return company["owner_id"]

    async def update_company_admin(self, company_id: int, data: dict, current_user) -> dict:
        company_updates = {key: data[key] for key in COMPANY_FIELDS if key in data}
        profile_updates = {key: data[key] for key in PROFILE_FIELDS if key in data}
        if "title" in company_updates and not _clean_text(company_updates["title"]):
            raise ValidationError("Название компании не может быть пустым")

        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                changes: dict[str, dict] = {}

                sets: list[str] = []
                params: list = []
                for field, raw in company_updates.items():
                    value = _clean_text(raw)
                    if value is None and field not in NULLABLE_COMPANY_FIELDS:
                        continue
                    if value == company[field]:
                        continue
                    params.append(value)
                    sets.append(f"{field} = ${len(params)}")
                    changes[field] = {"before": company[field], "after": value}
                if sets:
                    params.append(company_id)
                    await conn.execute(
                        f"""
                        UPDATE companies SET {', '.join(sets)}, updated_at = NOW()
                        WHERE id = ${len(params)}
                        """,
                        *params,
                    )
                if "title" in changes:
                    await conn.execute(
                        """
                        UPDATE actors SET display_name = $1, updated_at = NOW()
                        WHERE kind::text = 'company' AND company_id = $2
                        """,
                        changes["title"]["after"],
                        company_id,
                    )

                if profile_updates:
                    profile = await conn.fetchrow(
                        f"SELECT {', '.join(PROFILE_FIELDS)} FROM company_profiles WHERE company_id = $1",
                        company_id,
                    )
                    current = dict(profile) if profile else {field: None for field in PROFILE_FIELDS}
                    next_values: dict = {}
                    for field, raw in profile_updates.items():
                        if field in PROFILE_ARRAY_FIELDS:
                            value = [item.strip() for item in (raw or []) if item and item.strip()]
                            before = list(current.get(field) or [])
                        else:
                            value = _clean_text(raw)
                            before = current.get(field)
                        if value != before:
                            next_values[field] = value
                            changes[f"profile.{field}"] = {"before": before, "after": value}
                    if next_values:
                        if not profile:
                            await conn.execute(
                                """
                                INSERT INTO company_profiles (company_id, languages, industries)
                                VALUES ($1, '{}'::text[], '{}'::text[])
                                """,
                                company_id,
                            )
                        profile_sets = []
                        profile_params: list = []
                        for field, value in next_values.items():
                            profile_params.append(value)
                            cast = "::text[]" if field in PROFILE_ARRAY_FIELDS else ""
                            profile_sets.append(f"{field} = ${len(profile_params)}{cast}")
                        profile_params.append(company_id)
                        await conn.execute(
                            f"""
                            UPDATE company_profiles SET {', '.join(profile_sets)}
                            WHERE company_id = ${len(profile_params)}
                            """,
                            *profile_params,
                        )

                if changes:
                    await log_admin(
                        conn,
                        actor_id_of(current_user),
                        "admin.company.update",
                        "company",
                        company_id,
                        {"changes": changes, "fields": sorted(changes)},
                    )
                    await notify_user(
                        conn,
                        company["owner_id"],
                        "Данные компании обновлены",
                        f"Администратор обновил данные компании «{company['title']}».",
                        href=_company_href(company),
                        company_id=company_id,
                    )
        return await self.get_company_detail(company_id)

    async def set_company_logo_admin(self, company_id: int, logo_key: str, current_user) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                await conn.execute(
                    "UPDATE companies SET logo = $1, updated_at = NOW() WHERE id = $2",
                    logo_key,
                    company_id,
                )
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.update",
                    "company",
                    company_id,
                    {
                        "changes": {"logo": {"before": company["logo"], "after": logo_key}},
                        "fields": ["logo"],
                    },
                )
        return {"logo": logo_key, "logo_url": file_url(logo_key)}

    async def add_company_member_admin(
        self, company_id: int, email: str, role: str, current_user
    ) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                user = await _find_marketplace_user(conn, email=email)
                if await _membership(conn, company_id, user["id"]):
                    raise ConflictError("Пользователь уже состоит в компании")
                await conn.execute(
                    """
                    INSERT INTO company_users (company_id, user_id, role)
                    VALUES ($1, $2, $3::company_role)
                    """,
                    company_id,
                    user["id"],
                    role,
                )
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.member.add",
                    "company",
                    company_id,
                    {"user_id": user["id"], "email": user["email"], "role": role},
                )
                await notify_user(
                    conn,
                    user["id"],
                    "Вас добавили в компанию",
                    f"Администратор добавил вас в компанию «{company['title']}».",
                    href=_company_href(company),
                    company_id=company_id,
                )
        return await self.get_company_detail(company_id)

    async def update_company_member_admin(
        self, company_id: int, user_id: int, role: str, current_user
    ) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                membership = await _membership(conn, company_id, user_id)
                if not membership:
                    raise NotFoundError("Участник не найден")
                if user_id == company["owner_id"]:
                    raise ForbiddenError("Роль владельца меняется через смену владельца")
                if membership["role"] != role:
                    await conn.execute(
                        "UPDATE company_users SET role = $1::company_role WHERE id = $2",
                        role,
                        membership["id"],
                    )
                    await log_admin(
                        conn,
                        actor_id_of(current_user),
                        "admin.company.member.update",
                        "company",
                        company_id,
                        {
                            "user_id": user_id,
                            "changes": {"role": {"before": membership["role"], "after": role}},
                        },
                    )
        return await self.get_company_detail(company_id)

    async def remove_company_member_admin(self, company_id: int, user_id: int, current_user) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                if user_id == company["owner_id"]:
                    raise ForbiddenError("Нельзя удалить владельца компании")
                membership = await _membership(conn, company_id, user_id)
                if not membership:
                    raise NotFoundError("Участник не найден")
                await conn.execute("DELETE FROM company_users WHERE id = $1", membership["id"])
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.member.remove",
                    "company",
                    company_id,
                    {"user_id": user_id, "role": membership["role"]},
                )
                await notify_user(
                    conn,
                    user_id,
                    "Вы исключены из компании",
                    f"Администратор исключил вас из компании «{company['title']}».",
                )
        return await self.get_company_detail(company_id)

    async def change_company_owner_admin(
        self,
        company_id: int,
        current_user,
        *,
        user_id: int | None = None,
        email: str | None = None,
        reason: str | None = None,
    ) -> dict:
        if user_id is None and not (email and email.strip()):
            raise ValidationError("Укажите пользователя: user_id или email")
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                new_owner = await _find_marketplace_user(conn, user_id=user_id, email=email)
                previous_owner_id = company["owner_id"]
                if new_owner["id"] == previous_owner_id:
                    raise ConflictError("Пользователь уже является владельцем")

                new_membership = await _membership(conn, company_id, new_owner["id"])
                previous_role = new_membership["role"] if new_membership else None
                if new_membership:
                    await conn.execute(
                        "UPDATE company_users SET role = 'director' WHERE id = $1",
                        new_membership["id"],
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO company_users (company_id, user_id, role)
                        VALUES ($1, $2, 'director')
                        """,
                        company_id,
                        new_owner["id"],
                    )
                await conn.execute(
                    """
                    INSERT INTO company_users (company_id, user_id, role)
                    VALUES ($1, $2, 'admin')
                    ON CONFLICT (company_id, user_id) DO UPDATE SET role = 'admin'
                    """,
                    company_id,
                    previous_owner_id,
                )
                await conn.execute(
                    "UPDATE companies SET owner_id = $1, updated_at = NOW() WHERE id = $2",
                    new_owner["id"],
                    company_id,
                )
                sides = [
                    row["side"]
                    for row in await conn.fetch(
                        """
                        SELECT side::text AS side FROM actors
                        WHERE kind::text = 'company' AND company_id = $1 AND is_active = TRUE
                        ORDER BY id
                        """,
                        company_id,
                    )
                ] or [company["actor_type"]]
                await CompanyService(conn)._ensure_roles_and_actors(conn, dict(new_owner), sides)

                cleaned_reason = reason.strip() if reason and reason.strip() else None
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.owner.change",
                    "company",
                    company_id,
                    {
                        "changes": {
                            "owner_id": {"before": previous_owner_id, "after": new_owner["id"]}
                        },
                        "new_owner_email": new_owner["email"],
                        "new_owner_previous_role": previous_role,
                        "previous_owner_role": "admin",
                        "reason": cleaned_reason,
                    },
                )
                href = _company_href(company)
                await notify_user(
                    conn,
                    new_owner["id"],
                    "Вы назначены владельцем компании",
                    with_reason(
                        f"Администратор назначил вас владельцем компании «{company['title']}».",
                        cleaned_reason,
                    ),
                    href=href,
                    company_id=company_id,
                )
                await notify_user(
                    conn,
                    previous_owner_id,
                    "Владелец компании изменён",
                    with_reason(
                        f"Администратор передал владение компанией «{company['title']}» "
                        "другому пользователю. Вы остались в команде с ролью администратора.",
                        cleaned_reason,
                    ),
                    href=href,
                    company_id=company_id,
                )
        return await self.get_company_detail(company_id)

    async def set_company_categories_admin(
        self, company_id: int, category_ids: list[int], current_user
    ) -> dict:
        wanted = sorted(set(category_ids))
        async with self.connection() as conn:
            async with conn.transaction():
                await _load_company(conn, company_id, lock=True)
                if wanted:
                    found = await conn.fetch(
                        "SELECT id FROM categories WHERE id = ANY($1::int[])", wanted
                    )
                    missing = set(wanted) - {row["id"] for row in found}
                    if missing:
                        raise ValidationError(
                            f"Категории не найдены: {', '.join(map(str, sorted(missing)))}"
                        )
                before = sorted(
                    row["category_id"]
                    for row in await conn.fetch(
                        "SELECT category_id FROM company_categories WHERE company_id = $1",
                        company_id,
                    )
                )
                if before != wanted:
                    await conn.execute(
                        "DELETE FROM company_categories WHERE company_id = $1", company_id
                    )
                    await conn.executemany(
                        "INSERT INTO company_categories (company_id, category_id) VALUES ($1, $2)",
                        [(company_id, cat_id) for cat_id in wanted],
                    )
                    await log_admin(
                        conn,
                        actor_id_of(current_user),
                        "admin.company.categories.update",
                        "company",
                        company_id,
                        {
                            "changes": {"category_ids": {"before": before, "after": wanted}},
                            "added": sorted(set(wanted) - set(before)),
                            "removed": sorted(set(before) - set(wanted)),
                        },
                    )
        return await self.get_company_detail(company_id)

    async def add_company_document_admin(
        self,
        company_id: int,
        current_user,
        *,
        title: str,
        issuer: str,
        issue_date: str,
        expiry_date: str | None,
        file_key: str,
    ) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id)
                document_id = await conn.fetchval(
                    """
                    INSERT INTO company_certificates (
                        company_id, title, issuer, issue_date, expiry_date, file_url
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    company_id,
                    title,
                    issuer,
                    issue_date,
                    expiry_date,
                    file_key,
                )
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.document.add",
                    "company",
                    company_id,
                    {
                        "document_id": document_id,
                        "title": title,
                        "issuer": issuer,
                        "issue_date": issue_date,
                        "expiry_date": expiry_date,
                        "file_url": file_key,
                    },
                )
                await notify_user(
                    conn,
                    company["owner_id"],
                    "Добавлен документ компании",
                    f"Администратор добавил документ «{title}» в компанию «{company['title']}».",
                    href=_company_href(company),
                    company_id=company_id,
                )
        return await self.get_company_detail(company_id)

    async def delete_company_document_admin(
        self, company_id: int, document_id: int, current_user
    ) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                await _load_company(conn, company_id)
                document = await conn.fetchrow(
                    """
                    DELETE FROM company_certificates
                    WHERE id = $1 AND company_id = $2
                    RETURNING id, title, issuer, issue_date, expiry_date, file_url
                    """,
                    document_id,
                    company_id,
                )
                if not document:
                    raise NotFoundError("Документ не найден")
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.document.delete",
                    "company",
                    company_id,
                    {"document_id": document_id, "before": dict(document)},
                )
        return await self.get_company_detail(company_id)

    async def set_company_verification_admin(
        self, company_id: int, status: str, current_user, reason: str | None = None
    ) -> dict:
        cleaned_reason = reason.strip() if reason and reason.strip() else None
        async with self.connection() as conn:
            async with conn.transaction():
                company = await _load_company(conn, company_id, lock=True)
                previous = company["verification_status"]
                if status != "verified" and not cleaned_reason:
                    raise ValidationError("Укажите причину изменения статуса проверки")
                if previous != status:
                    await conn.execute(
                        """
                        UPDATE companies
                        SET verification_status = $1::verification_status, updated_at = NOW()
                        WHERE id = $2
                        """,
                        status,
                        company_id,
                    )
                await sync_company_trust_level(conn, company_id, status)
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.company.verification.set",
                    "company",
                    company_id,
                    {
                        "changes": {"verification_status": {"before": previous, "after": status}},
                        "previous_verification_status": previous,
                        "verification_status": status,
                        "revoked": previous == "verified" and status != "verified",
                        "reason": cleaned_reason,
                    },
                )
                if previous != status:
                    await notify_user(
                        conn,
                        company["owner_id"],
                        "Статус проверки компании изменён",
                        with_reason(
                            f"Статус проверки компании «{company['title']}»: "
                            f"{VERIFICATION_LABELS[status]}.",
                            cleaned_reason,
                        ),
                        href=_company_href(company),
                        company_id=company_id,
                    )
        return await self.get_company_detail(company_id)
