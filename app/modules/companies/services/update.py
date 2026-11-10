import json

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.modules.actors.services import ActorService
from app.modules.companies.schemas import (
    CertificateUpdateRequest,
    CompanyCertificateSchema,
    CompanyUpdateRequest,
    CompanyWithRelations,
    UpdateTeamMemberRequest,
)
from app.modules.companies.services.common import (
    COMPANY_ROLES,
    ensure_can_change_member,
    ensure_can_manage,
    load_company_schema,
)
from app.utils.audit import log_event
from app.utils.side_roles import SIDE_ROLES

AUDITED_COMPANY_FIELDS = (
    "title",
    "legal_name",
    "tax_number",
    "website",
    "description",
    "logo",
    "country",
    "city",
    "address",
)
AUDITED_PROFILE_FIELDS = (
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
    "category_ids",
    "actor_types",
)


def _company_update_details(data: CompanyUpdateRequest, before_row, skip_moderated: bool) -> dict:
    from app.modules.companies.services.changes import MODERATED_FIELDS

    before: dict = {}
    after: dict = {}
    for field in AUDITED_COMPANY_FIELDS:
        if skip_moderated and field in MODERATED_FIELDS:
            continue
        value = getattr(data, field, None)
        if value is None or before_row is None or before_row[field] == value:
            continue
        before[field] = before_row[field]
        after[field] = value
    profile_fields = [field for field in AUDITED_PROFILE_FIELDS if getattr(data, field, None) is not None]
    return {"before": before, "after": after, "profile_fields": profile_fields}


class CompanyUpdateMixin:
    async def update_company(
        self, company_id: int, user_id: int, data: CompanyUpdateRequest
    ) -> CompanyWithRelations:
        from app.modules.companies.services.changes import MODERATED_FIELDS

        skip_moderated = False
        async with self.connection() as conn:
            company_row = await conn.fetchrow(
                """
                SELECT verification_status::text AS verification_status,
                       title, legal_name, tax_number, address, city, country
                FROM companies WHERE id = $1
                """,
                company_id,
            )
        if not company_row:
            raise NotFoundError("Company not found")

        if company_row["verification_status"] == "verified":
            skip_moderated = True
            changed = {}
            for field in MODERATED_FIELDS:
                val = getattr(data, field, None)
                if val is None:
                    continue
                current = str(company_row[field] or "").strip()
                if str(val).strip() != current:
                    changed[field] = val
            if changed:
                await self.submit_change_request(company_id, user_id, changed)

        async with self.connection() as conn:
            async with conn.transaction():
                await load_company_schema(conn, company_id)
                await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
                before_row = await conn.fetchrow(
                    """
                    SELECT title, legal_name, tax_number, website, description, logo,
                           country, city, address
                    FROM companies WHERE id = $1
                    """,
                    company_id,
                )
                fields = (
                    "title",
                    "legal_name",
                    "tax_number",
                    "website",
                    "description",
                    "logo",
                    "country",
                    "city",
                    "address",
                )
                sets = []
                params: list = []
                for field in fields:
                    if skip_moderated and field in MODERATED_FIELDS:
                        continue
                    val = getattr(data, field)
                    if val is not None:
                        params.append(val)
                        sets.append(f"{field} = ${len(params)}")
                if sets:
                    params.append(company_id)
                    await conn.execute(
                        f"UPDATE companies SET {', '.join(sets)}, updated_at = NOW() WHERE id = ${len(params)}",
                        *params,
                    )
                else:
                    await conn.execute(
                        "UPDATE companies SET updated_at = NOW() WHERE id = $1",
                        company_id,
                    )

                profile_updates = []
                profile_params: list = []
                for field in (
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
                ):
                    val = getattr(data, field)
                    if val is not None:
                        if field == "gallery":
                            profile_params.append(json.dumps(val))
                            profile_updates.append(
                                f"{field} = ${len(profile_params)}::jsonb"
                            )
                        else:
                            profile_params.append(val)
                            cast = "::text[]" if field in {"languages", "industries"} else ""
                            profile_updates.append(
                                f"{field} = ${len(profile_params)}{cast}"
                            )
                if profile_updates:
                    profile_params.append(company_id)
                    await conn.execute(
                        f"""
                        UPDATE company_profiles
                        SET {', '.join(profile_updates)}
                        WHERE company_id = ${len(profile_params)}
                        """,
                        *profile_params,
                    )

                if data.category_ids is not None:
                    await conn.execute(
                        "DELETE FROM company_categories WHERE company_id = $1",
                        company_id,
                    )
                    for cat_id in data.category_ids:
                        await conn.execute(
                            """
                            INSERT INTO company_categories (company_id, category_id)
                            VALUES ($1, $2)
                            """,
                            company_id,
                            cat_id,
                        )

                if data.actor_types is not None:
                    actor_types = list(data.actor_types)
                    if "supplier" in actor_types:
                        has_supplier = await conn.fetchval(
                            """
                            SELECT 1 FROM actors
                            WHERE company_id = $1 AND kind::text = 'company'
                              AND side::text = 'supplier' AND is_active = TRUE
                            """,
                            company_id,
                        )
                        if not has_supplier:
                            from app.modules.subscription.services import SubscriptionService

                            owner_id = await conn.fetchval(
                                "SELECT owner_id FROM companies WHERE id = $1", company_id
                            )
                            await SubscriptionService(conn).check_company_limit(
                                owner_id, exclude_company_id=company_id
                            )
                    if actor_types:
                        await conn.execute(
                            "UPDATE companies SET actor_type = $1::actor_type WHERE id = $2",
                            actor_types[0],
                            company_id,
                        )
                    company = await conn.fetchrow(
                        """
                        SELECT id, title, verification_status::text AS verification_status
                        FROM companies WHERE id = $1
                        """,
                        company_id,
                    )
                    await ActorService(conn).sync_company_actors(dict(company), actor_types)
                await log_event(
                    conn,
                    "company.update",
                    "company",
                    company_id,
                    user_id=user_id,
                    company_id=company_id,
                    details=_company_update_details(data, before_row, skip_moderated),
                )
                return await load_company_schema(conn, company_id)

    async def remove_team_member(self, company_id: int, user_id: int, target_user_id: int):
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
            membership = await conn.fetchrow(
                """
                SELECT id, role::text AS role
                FROM company_users
                WHERE company_id = $1 AND user_id = $2
                """,
                company_id,
                target_user_id,
            )
            if not membership:
                raise NotFoundError("Team member not found")
            if membership["role"] == "director":
                raise ForbiddenError("Cannot remove company director")
            if target_user_id != user_id:  # leaving the team yourself is always allowed
                await ensure_can_change_member(
                    conn,
                    company_id,
                    user_id,
                    target_user_id=target_user_id,
                    target_role=membership["role"],
                )
            await conn.execute("DELETE FROM company_users WHERE id = $1", membership["id"])
            await log_event(
                conn,
                "company.member.remove",
                "company",
                company_id,
                user_id=user_id,
                company_id=company_id,
                details={"member_user_id": target_user_id, "role": membership["role"]},
            )

    async def update_team_member(
        self, company_id: int, user_id: int, target_user_id: int, data: UpdateTeamMemberRequest
    ):
        sent = data.model_fields_set
        if data.role is not None and data.role not in COMPANY_ROLES:
            raise ValidationError("Invalid company role")
        side_changes: dict[str, str | None] = {}
        for side in ("buyer", "supplier"):
            field = f"{side}_role"
            if field not in sent:
                continue
            value = getattr(data, field)
            if value is not None and value not in SIDE_ROLES:
                raise ValidationError("Invalid side role")
            side_changes[side] = value
        if data.role is None and not side_changes:
            raise ValidationError("Nothing to change")
        async with self.connection() as conn:
            async with conn.transaction():
                await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
                membership = await conn.fetchrow(
                    """
                    SELECT cu.id, cu.role::text AS role, u.email,
                        NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), '') AS name
                    FROM company_users cu
                    JOIN users u ON u.id = cu.user_id
                    WHERE cu.company_id = $1 AND cu.user_id = $2
                    FOR UPDATE OF cu
                    """,
                    company_id,
                    target_user_id,
                )
                if not membership:
                    raise NotFoundError("Team member not found")
                role = data.role if data.role is not None else membership["role"]
                if membership["role"] == "director" and role != "director":
                    raise ForbiddenError("Cannot change company director role")
                if role == "director" and membership["role"] != "director":
                    raise ForbiddenError("Cannot assign director role")
                if side_changes and membership["role"] == "director":
                    raise ForbiddenError("Руководитель всегда имеет полный доступ к обеим сторонам")
                if target_user_id == user_id and "none" in side_changes.values():
                    raise ForbiddenError("Нельзя закрыть доступ самому себе")
                await ensure_can_change_member(
                    conn,
                    company_id,
                    user_id,
                    target_user_id=target_user_id,
                    target_role=membership["role"],
                    # An admin role on one side is still an admin role.
                    new_role="admin"
                    if role == "admin" and data.role is not None or "admin" in side_changes.values()
                    else None,
                )
                row = await conn.fetchrow(
                    """
                    UPDATE company_users
                    SET role = $1::company_role
                    WHERE id = $2
                    RETURNING id, company_id, user_id, role::text AS role
                    """,
                    role,
                    membership["id"],
                )
                before_sides = {
                    r["side"]: r["role"]
                    for r in await conn.fetch(
                        "SELECT side, role FROM company_user_side_roles WHERE company_user_id = $1",
                        membership["id"],
                    )
                }
                for side, value in side_changes.items():
                    if value is None:
                        await conn.execute(
                            "DELETE FROM company_user_side_roles WHERE company_user_id = $1 AND side = $2",
                            membership["id"],
                            side,
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO company_user_side_roles (company_user_id, side, role)
                            VALUES ($1, $2, $3)
                            ON CONFLICT (company_user_id, side) DO UPDATE SET role = EXCLUDED.role
                            """,
                            membership["id"],
                            side,
                            value,
                        )
                after_sides = {**before_sides}
                for side, value in side_changes.items():
                    if value is None:
                        after_sides.pop(side, None)
                    else:
                        after_sides[side] = value
                if membership["role"] != row["role"]:
                    await log_event(
                        conn,
                        "company.member.role_change",
                        "company",
                        company_id,
                        user_id=user_id,
                        company_id=company_id,
                        details={
                            "member_user_id": target_user_id,
                            "before": {"role": membership["role"]},
                            "after": {"role": row["role"]},
                        },
                    )
                if before_sides != after_sides:
                    await log_event(
                        conn,
                        "company.member.side_role_change",
                        "company",
                        company_id,
                        user_id=user_id,
                        company_id=company_id,
                        details={
                            "member_user_id": target_user_id,
                            "before": before_sides,
                            "after": after_sides,
                        },
                    )
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "user_id": row["user_id"],
            "role": row["role"],
            "email": membership["email"],
            "name": membership["name"],
            "buyer_role": after_sides.get("buyer"),
            "supplier_role": after_sides.get("supplier"),
        }

    async def update_certificate(
        self,
        company_id: int,
        user_id: int,
        certificate_id: int,
        data: CertificateUpdateRequest,
    ) -> CompanyCertificateSchema:
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_certificates.manage")
            current = await conn.fetchrow(
                """
                SELECT id, company_id, title, issuer, issue_date, expiry_date, file_url
                FROM company_certificates
                WHERE id = $1 AND company_id = $2
                """,
                certificate_id,
                company_id,
            )
            if not current:
                raise NotFoundError("Certificate not found")
            fields = ("title", "issuer", "issue_date", "expiry_date", "file_url")
            sets = []
            params: list = []
            for field in fields:
                val = getattr(data, field)
                if val is not None:
                    params.append(val)
                    sets.append(f"{field} = ${len(params)}")
            if sets:
                params.extend([certificate_id, company_id])
                row = await conn.fetchrow(
                    f"""
                    UPDATE company_certificates
                    SET {', '.join(sets)}
                    WHERE id = ${len(params) - 1} AND company_id = ${len(params)}
                    RETURNING id, company_id, title, issuer, issue_date, expiry_date, file_url
                    """,
                    *params,
                )
            else:
                row = current
        return CompanyCertificateSchema.model_validate(dict(row))

    async def remove_certificate(self, company_id: int, user_id: int, certificate_id: int):
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_certificates.manage")
            result = await conn.execute(
                """
                DELETE FROM company_certificates
                WHERE id = $1 AND company_id = $2
                """,
                certificate_id,
                company_id,
            )
            if result == "DELETE 0":
                raise NotFoundError("Certificate not found")
            await log_event(
                conn,
                "company.certificate.remove",
                "company",
                company_id,
                user_id=user_id,
                company_id=company_id,
                details={"certificate_id": certificate_id},
            )
