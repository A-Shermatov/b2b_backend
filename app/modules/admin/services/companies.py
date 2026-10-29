from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import build_page, fetch_page
from app.core.permissions import ensure_permission
from app.modules.actors.services.common import trust_level_for_company
from app.modules.admin.services.common import (
    actor_id_of,
    actor_role_of,
    company_status_sql,
    file_url,
    log_admin,
    normalize_query,
    notify_user,
    with_reason,
)

COMPANY_LIST_COLUMNS = """
    c.id, c.title, c.actor_type::text AS actor_type, c.owner_id,
    c.legal_name, c.tax_number, c.logo, c.country, c.city,
    c.verification_status::text AS verification_status,
    c.operational_status::text AS operational_status,
    c.rating,
    COALESCE(c.vip, FALSE) AS vip,
    c.vip_until,
    c.created_at,
    u.id AS owner_user_id, u.email AS owner_email,
    u.first_name AS owner_first_name, u.last_name AS owner_last_name
"""


async def sync_company_trust_level(conn, company_id: int, verification_status: str) -> None:
    await conn.execute(
        """
        UPDATE actors
        SET trust_level = $1::trust_level, updated_at = NOW()
        WHERE kind::text = 'company' AND company_id = $2
        """,
        trust_level_for_company(verification_status),
        company_id,
    )


def _company_search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    idx = len(params)
    sql = f"""
        (
            c.title ILIKE ${idx}
            OR c.legal_name ILIKE ${idx}
            OR c.tax_number ILIKE ${idx}
            OR u.email ILIKE ${idx}
            OR u.first_name ILIKE ${idx}
            OR u.last_name ILIKE ${idx}
            OR c.id::text ILIKE ${idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        sql += f" OR c.id = ${len(params)}"
    return sql + ")"


def _serialize_company_list_item(company, actor_types: list[str]) -> dict:
    return {
        "id": company["id"],
        "title": company["title"],
        "actor_type": company["actor_type"],
        "actor_types": sorted(set(actor_types)),
        "owner": {
            "id": company["owner_user_id"],
            "email": company["owner_email"],
            "name": f"{company['owner_first_name']} {company['owner_last_name']}".strip(),
        },
        "legal_name": company["legal_name"],
        "tax_number": company["tax_number"],
        "logo": file_url(company["logo"]) if company["logo"] else company["logo"],
        "country": company["country"],
        "city": company["city"],
        "verification_status": company["verification_status"],
        "operational_status": company["operational_status"],
        "rating": company["rating"],
        "vip": bool(company.get("vip")),
        "vip_until": company.get("vip_until"),
        "created_at": company["created_at"],
    }


def _company_action_notification(action: str, company_title: str, reason: str | None):
    messages = {
        "approve": ("Компания верифицирована", f"Компания «{company_title}» прошла проверку."),
        "reject": ("Верификация отклонена", f"Проверка компании «{company_title}» отклонена."),
        "request_documents": (
            "Нужны дополнительные документы",
            f"Для компании «{company_title}» запрошены дополнительные документы.",
        ),
        "block": (
            "Компания заблокирована",
            f"Компания «{company_title}» заблокирована администратором.",
        ),
        "deactivate": (
            "Компания деактивирована",
            f"Компания «{company_title}» деактивирована администратором.",
        ),
        "reactivate": (
            "Компания активирована",
            f"Доступ компании «{company_title}» восстановлен.",
        ),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


class AdminCompaniesMixin:
    async def list_companies(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str = "all",
        query: str | None = None,
        *,
        actor_type: str | None = None,
        verification_status: str | None = None,
        operational_status: str | None = None,
        vip: bool | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _company_search_sql(normalized, search_params)
        join_sql = "FROM companies c JOIN users u ON u.id = c.owner_id"
        filter_clauses = [search_sql] if search_sql else []
        if actor_type:
            search_params.append(actor_type)
            idx = len(search_params)
            filter_clauses.append(
                f"""(
                    c.actor_type::text = ${idx}
                    OR EXISTS (
                        SELECT 1 FROM actors fa
                        WHERE fa.company_id = c.id AND fa.kind::text = 'company'
                          AND fa.is_active = TRUE AND fa.side::text = ${idx}
                    )
                )"""
            )
        if verification_status:
            search_params.append(verification_status)
            filter_clauses.append(f"c.verification_status::text = ${len(search_params)}")
        if operational_status:
            search_params.append(operational_status)
            filter_clauses.append(f"c.operational_status::text = ${len(search_params)}")
        if vip is not None:
            search_params.append(vip)
            filter_clauses.append(f"COALESCE(c.vip, FALSE) = ${len(search_params)}")

        async def _count(conn, extra_sql: str | None):
            clauses = list(filter_clauses)
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(
                await conn.fetchval(f"SELECT COUNT(*) {join_sql} {where}", *search_params)
            )

        sort_sql = {
            "created_at": "c.created_at",
            "title": "LOWER(c.title)",
            "rating": "c.rating",
        }.get(sort, "c.created_at")
        direction = "ASC" if order == "asc" else "DESC"

        async with self.connection() as conn:
            status_sql = company_status_sql(status)
            total = await _count(conn, status_sql)
            count_values = {}
            for count_status in ("all", "verified", "pending", "rejected", "blocked"):
                count_values[count_status] = await _count(conn, company_status_sql(count_status))
            params = list(search_params)
            clauses = list(filter_clauses)
            if status_sql:
                clauses.append(status_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            companies = await conn.fetch(
                f"""
                SELECT {COMPANY_LIST_COLUMNS},
                    (SELECT COUNT(*) FROM company_users mu WHERE mu.company_id = c.id)
                        AS members_count,
                    (
                        SELECT COUNT(*) FROM catalog_items ci
                        JOIN actors ca ON ca.id = ci.actor_id
                        WHERE ca.company_id = c.id AND ci.status::text <> 'deleted'
                    ) AS products_count,
                    (
                        SELECT COUNT(*) FROM contracts ct
                        WHERE ct.buyer_actor_id IN (SELECT id FROM actors WHERE company_id = c.id)
                           OR ct.supplier_actor_id IN (
                               SELECT id FROM actors WHERE company_id = c.id
                           )
                    ) AS contracts_count
                {join_sql}
                {where}
                ORDER BY {sort_sql} {direction} NULLS LAST, c.id {direction}
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            actor_rows = await conn.fetch(
                """
                SELECT company_id, side::text AS side
                FROM actors
                WHERE company_id = ANY($1::int[])
                """,
                [row["id"] for row in companies],
            )

        by_company: dict[int, list[str]] = {}
        for row in actor_rows:
            by_company.setdefault(row["company_id"], []).append(row["side"])
        items = []
        for company in companies:
            item = _serialize_company_list_item(company, by_company.get(company["id"], []))
            item["members_count"] = int(company["members_count"] or 0)
            item["products_count"] = int(company["products_count"] or 0)
            item["contracts_count"] = int(company["contracts_count"] or 0)
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "status_counts": count_values,
        }

    async def get_company_detail(self, company_id: int) -> dict:
        async with self.connection() as conn:
            company = await conn.fetchrow(
                f"""
                SELECT {COMPANY_LIST_COLUMNS},
                    c.website, c.description, c.address, c.updated_at
                FROM companies c
                JOIN users u ON u.id = c.owner_id
                WHERE c.id = $1
                """,
                company_id,
            )
            if not company:
                raise NotFoundError("Company not found")
            actor_rows = await conn.fetch(
                "SELECT id, side::text AS side FROM actors WHERE company_id = $1",
                company_id,
            )
            actor_ids = [row["id"] for row in actor_rows]
            profile = await conn.fetchrow(
                """
                SELECT founded_year, employees_count, annual_revenue_range, languages, industries
                FROM company_profiles WHERE company_id = $1
                """,
                company_id,
            )
            stats = await conn.fetchrow(
                """
                SELECT completed_contracts, active_contracts, disputes_count, average_rating
                FROM company_stats WHERE company_id = $1
                """,
                company_id,
            )
            members = await conn.fetch(
                """
                SELECT cu.id, cu.user_id, cu.role::text AS role,
                       u.email, u.first_name, u.last_name, u.status::text AS status
                FROM company_users cu
                JOIN users u ON u.id = cu.user_id
                WHERE cu.company_id = $1
                ORDER BY (cu.user_id = $2) DESC, cu.id
                """,
                company_id,
                company["owner_id"],
            )
            certificates = await conn.fetch(
                """
                SELECT id, title, issuer, issue_date, expiry_date, file_url
                FROM company_certificates WHERE company_id = $1
                ORDER BY id
                """,
                company_id,
            )
            categories = await conn.fetch(
                """
                SELECT cat.id, cat.name
                FROM company_categories cc
                JOIN categories cat ON cat.id = cc.category_id
                WHERE cc.company_id = $1
                ORDER BY cat.name
                """,
                company_id,
            )
            catalog_items = []
            contracts = []
            reviews = []
            if actor_ids:
                catalog_items = await conn.fetch(
                    """
                    SELECT
                        ci.id, ci.title, ci.type::text AS type, ci.status::text AS status,
                        ci.created_at, cat.name AS category_name,
                        COALESCE(ip.fixed_price, ip.hourly_rate, ip.monthly_rate) AS price,
                        ip.currency,
                        COALESCE(st.views, 0) AS views,
                        COALESCE(st.leads, 0) AS leads
                    FROM catalog_items ci
                    LEFT JOIN categories cat ON cat.id = ci.category_id
                    LEFT JOIN item_pricing ip ON ip.item_id = ci.id
                    LEFT JOIN item_stats st ON st.item_id = ci.id
                    WHERE ci.actor_id = ANY($1::int[]) AND ci.status::text != 'deleted'
                    ORDER BY ci.created_at DESC
                    """,
                    actor_ids,
                )
                contracts = await conn.fetch(
                    """
                    SELECT id, title, status::text AS status, agreed_amount,
                           currency::text AS currency, buyer_actor_id, supplier_actor_id, created_at
                    FROM contracts
                    WHERE buyer_actor_id = ANY($1::int[]) OR supplier_actor_id = ANY($1::int[])
                    ORDER BY created_at DESC
                    LIMIT 50
                    """,
                    actor_ids,
                )
                reviews = await conn.fetch(
                    """
                    SELECT id, contract_id, reviewer_actor_id, rating, comment, created_at
                    FROM reviews
                    WHERE target_actor_id = ANY($1::int[])
                    ORDER BY created_at DESC
                    LIMIT 50
                    """,
                    actor_ids,
                )

        payload = _serialize_company_list_item(
            company, [row["side"] for row in actor_rows]
        )
        payload.update(
            {
                "legal_name": company["legal_name"],
                "tax_number": company["tax_number"],
                "website": company["website"],
                "description": company["description"],
                "address": company["address"],
                "updated_at": company["updated_at"],
                "profile": (
                    {
                        "founded_year": profile["founded_year"],
                        "employees_count": profile["employees_count"],
                        "annual_revenue_range": profile["annual_revenue_range"],
                        "languages": profile["languages"],
                        "industries": profile["industries"],
                    }
                    if profile
                    else None
                ),
                "stats": (
                    {
                        "completed_contracts": stats["completed_contracts"],
                        "active_contracts": stats["active_contracts"],
                        "disputes_count": stats["disputes_count"],
                        "average_rating": stats["average_rating"],
                    }
                    if stats
                    else None
                ),
                "members": [
                    {
                        "id": member["id"],
                        "user_id": member["user_id"],
                        "role": member["role"],
                        "email": member["email"],
                        "name": f"{member['first_name']} {member['last_name']}".strip(),
                        "status": member["status"],
                        "is_owner": member["user_id"] == company["owner_id"],
                    }
                    for member in members
                ],
                "categories": [{"id": row["id"], "name": row["name"]} for row in categories],
                "certificates": [
                    {
                        "id": cert["id"],
                        "title": cert["title"],
                        "issuer": cert["issuer"],
                        "issue_date": cert["issue_date"],
                        "expiry_date": cert["expiry_date"],
                        "file_url": file_url(cert["file_url"]),
                    }
                    for cert in certificates
                ],
                "products": [
                    _serialize_catalog_snippet(item)
                    for item in catalog_items
                    if item["type"] == "product"
                ],
                "services": [
                    _serialize_catalog_snippet(item)
                    for item in catalog_items
                    if item["type"] == "service"
                ],
                "contracts": [dict(row) for row in contracts],
                "reviews": [dict(row) for row in reviews],
                "verification_checklist": {
                    "legal_name": bool(company["legal_name"]),
                    "tax_number": bool(company["tax_number"]),
                    "address": bool(company["address"]),
                    "website": bool(company["website"]),
                    "certificates": bool(certificates),
                },
            }
        )
        return payload

    async def apply_company_action(
        self,
        company_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        verification_actions = {"approve", "reject", "request_documents"}
        operational_actions = {"block", "deactivate", "reactivate"}
        if action not in verification_actions | operational_actions:
            raise ValidationError("Unsupported company action")
        if action in operational_actions:
            ensure_permission(current_user, "admin_companies.block")
        else:
            ensure_permission(current_user, "admin_companies.update")
        if action in {"reject", "request_documents", "block", "deactivate"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        async with self.connection() as conn:
            company = await conn.fetchrow(
                """
                SELECT id, title, owner_id, actor_type::text AS actor_type,
                       verification_status::text AS verification_status,
                       operational_status::text AS operational_status
                FROM companies WHERE id = $1
                """,
                company_id,
            )
            if not company:
                raise NotFoundError("Company not found")

            previous_verification = company["verification_status"]
            previous_operational = company["operational_status"]
            verification = previous_verification
            operational = previous_operational

            if action == "approve":
                verification = "verified"
            elif action == "reject":
                verification = "rejected"
            elif action == "request_documents":
                verification = "needs_documents"
            elif action == "block":
                operational = "blocked"
            elif action == "deactivate":
                operational = "deactivated"
            elif action == "reactivate":
                operational = "active"

            await conn.execute(
                """
                UPDATE companies
                SET verification_status = $1::verification_status,
                    operational_status = $2::company_operational_status
                WHERE id = $3
                """,
                verification,
                operational,
                company_id,
            )
            if verification != previous_verification:
                await sync_company_trust_level(conn, company_id, verification)
            if action in {"block", "deactivate"}:
                await conn.execute(
                    "UPDATE actors SET is_active = FALSE WHERE company_id = $1",
                    company_id,
                )
            elif action == "reactivate":
                await conn.execute(
                    "UPDATE actors SET is_active = TRUE WHERE company_id = $1",
                    company_id,
                )

            title, body = _company_action_notification(action, company["title"], reason)
            href_side = "supplier" if company["actor_type"] == "supplier" else "customer"
            await notify_user(
                conn,
                company["owner_id"],
                title,
                body,
                href=f"/{href_side}/company/{company_id}",
                company_id=company_id,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.company.{action}",
                "company",
                company_id,
                {
                    "reason": reason.strip() if reason else None,
                    "previous_verification_status": previous_verification,
                    "verification_status": verification,
                    "previous_operational_status": previous_operational,
                    "operational_status": operational,
                },
            )

        return {
            "id": company_id,
            "action": action,
            "verification_status": verification,
            "operational_status": operational,
        }

    async def set_company_vip(
        self,
        company_id: int,
        vip: bool,
        current_user,
        *,
        months: int | None = None,
        reason: str | None = None,
    ) -> dict:
        from datetime import datetime, timedelta, timezone

        ensure_permission(current_user, "admin_companies.update")
        async with self.connection() as conn:
            company = await conn.fetchrow(
                """
                SELECT id, title, COALESCE(vip, FALSE) AS vip, vip_until
                FROM companies WHERE id = $1
                """,
                company_id,
            )
            if not company:
                raise NotFoundError("Company not found")
            vip_until = None
            if vip:
                months_value = months or 1
                vip_until = datetime.now(timezone.utc) + timedelta(days=30 * months_value)
            updated = await conn.fetchrow(
                """
                UPDATE companies
                SET vip = $1,
                    vip_until = $2,
                    updated_at = NOW()
                WHERE id = $3
                RETURNING id, COALESCE(vip, FALSE) AS vip, vip_until
                """,
                vip,
                vip_until,
                company_id,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                "admin.company.vip.grant" if vip else "admin.company.vip.revoke",
                "company",
                company_id,
                {
                    "vip": vip,
                    "vip_until": vip_until.isoformat() if vip_until else None,
                    "months": months if vip else None,
                    "reason": reason.strip() if reason and reason.strip() else None,
                    "previous_vip": bool(company["vip"]),
                },
            )
        return {
            "id": updated["id"],
            "vip": bool(updated["vip"]),
            "vip_until": updated["vip_until"],
        }

    async def list_pending_verification(self, page: int = 1, page_size: int = 20) -> dict:
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                """
                SELECT COUNT(*) FROM companies
                WHERE verification_status::text = 'pending'
                """,
                """
                SELECT id, title, actor_type::text AS actor_type, owner_id,
                       verification_status::text AS verification_status
                FROM companies
                WHERE verification_status::text = 'pending'
                ORDER BY id
                """,
                [],
                page,
                page_size,
            )
        return build_page([dict(row) for row in rows], total, page, page_size)

    async def verify_company(self, company_id: int, approved: bool) -> dict:
        async with self.connection() as conn:
            company = await conn.fetchrow("SELECT id FROM companies WHERE id = $1", company_id)
            if not company:
                raise NotFoundError("Company not found")
            status = "verified" if approved else "rejected"
            await conn.execute(
                "UPDATE companies SET verification_status = $1::verification_status WHERE id = $2",
                status,
                company_id,
            )
        return {"id": company_id, "verification_status": status}


def _serialize_catalog_snippet(item) -> dict:
    return {
        "id": item["id"],
        "title": item["title"],
        "status": item["status"],
        "category": item["category_name"],
        "price": item["price"],
        "currency": item["currency"],
        "views": item["views"],
        "leads": item["leads"],
        "created_at": item["created_at"],
    }
