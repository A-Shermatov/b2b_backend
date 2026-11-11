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
    ensure_company_member,
    load_company_schema,
)
from app.modules.notifications.services import NotificationService
from app.utils.audit import log_event
from app.utils.side_roles import SIDE_ROLES


class CompanyUpdateMixin:
    async def update_company(
        self, company_id: int, user_id: int, data: CompanyUpdateRequest
    ) -> CompanyWithRelations:
        """Save the company page. What others see waits for an admin; only the
        company's sides (buyer / supplier) change at once."""
        from app.modules.companies.services.changes import (
            COMPANY_REVIEWED_FIELDS,
            queue_company_changes,
        )

        proposed = {
            field: getattr(data, field)
            for field in COMPANY_REVIEWED_FIELDS
            if getattr(data, field, None) is not None
        }
        async with self.connection() as conn:
            async with conn.transaction():
                await load_company_schema(conn, company_id)
                await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
                queued = await queue_company_changes(conn, company_id, user_id, proposed)

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
                    await conn.execute(
                        "UPDATE companies SET updated_at = NOW() WHERE id = $1", company_id
                    )
                await log_event(
                    conn,
                    "company.update",
                    "company",
                    company_id,
                    user_id=user_id,
                    company_id=company_id,
                    details={
                        "sent_for_review": sorted(proposed) if queued else [],
                        "actor_types": list(data.actor_types) if data.actor_types is not None else None,
                    },
                )
                result = await load_company_schema(conn, company_id)
                pending = await conn.fetchval(
                    """
                    SELECT proposed FROM company_change_requests
                    WHERE company_id = $1 AND status = 'pending'
                    """,
                    company_id,
                )
                if pending:
                    result.pending_changes = sorted(json.loads(pending) if isinstance(pending, str) else pending)
                return result

    async def remove_team_member(self, company_id: int, user_id: int, target_user_id: int):
        leaving = target_user_id == user_id
        async with self.connection() as conn:
            if leaving:
                # Leaving the team yourself is allowed at any access level.
                await ensure_company_member(conn, company_id, user_id)
            else:
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
                raise ForbiddenError(
                    "Руководителя нельзя убрать из компании. "
                    "Чтобы передать компанию другому человеку, напишите в поддержку"
                )
            if not leaving:
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
            company_title = await conn.fetchval(
                "SELECT title FROM companies WHERE id = $1", company_id
            )
        if not leaving:
            # The person learns why the company disappeared from their cabinet.
            await NotificationService().notify_user(
                target_user_id,
                "system",
                f"Доступ к компании «{company_title or ''}» закрыт",
                "Вас убрали из команды компании. Сделки, заявки и переписка остались "
                "у компании; ваш личный профиль и вход на площадку сохранены.",
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
