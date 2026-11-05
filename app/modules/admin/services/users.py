from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import ensure_permission
from app.modules.admin.services.chats import CHAT_RISK_RE
from app.modules.admin.services.common import (
    STAFF_RANK,
    USER_COLUMNS,
    actor_id_of,
    actor_role_of,
    file_url,
    log_admin,
    normalize_query,
    pages_for,
    serialize_audit_entry,
)


def _search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    search_idx = len(params)
    sql = f"""
        (
            u.email ILIKE ${search_idx}
            OR u.first_name ILIKE ${search_idx}
            OR u.last_name ILIKE ${search_idx}
            OR (u.first_name || ' ' || u.last_name) ILIKE ${search_idx}
            OR u.phone ILIKE ${search_idx}
            OR u.id::text ILIKE ${search_idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        sql += f" OR u.id = ${len(params)}"
    return sql + ")"


def _serialize_list_item(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "phone": row["phone"],
        "role": row["role"],
        "status": row["status"],
        "banned_until": row.get("banned_until"),
        "block_reason": row.get("block_reason"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "company_id": row["company_id"],
        "company_title": row["company_title"],
        "contracts_count": int(row["contracts_count"] or 0),
        "contracts_sum": float(row["contracts_sum"] or 0),
    }


class AdminUsersMixin:
    async def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        role_side: str | None = None,
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        params: list = []
        search_sql = _search_sql(normalized, params)
        search_param_count = len(params)

        status_value = status.value if hasattr(status, "value") else status
        filter_sql = search_sql
        if status_value:
            params.append(status_value)
            clause = f"u.status::text = ${len(params)}"
            filter_sql = f"{filter_sql} AND {clause}" if filter_sql else clause

        if role_side == "buyer":
            clause = "u.role::text IN ('buyer', 'both')"
            filter_sql = f"{filter_sql} AND {clause}" if filter_sql else clause
        elif role_side == "supplier":
            clause = "u.role::text IN ('supplier', 'both')"
            filter_sql = f"{filter_sql} AND {clause}" if filter_sql else clause

        where_filter = f"WHERE {filter_sql}" if filter_sql else ""
        where_search = f"WHERE {search_sql}" if search_sql else ""

        async with self.connection() as conn:
            total = int(
                await conn.fetchval(
                    f"SELECT COUNT(*) FROM users u {where_filter}",
                    *params,
                )
            )
            count_rows = await conn.fetch(
                f"""
                SELECT
                    COUNT(*)::int AS all_cnt,
                    COUNT(*) FILTER (WHERE u.status::text = 'active')::int AS active_cnt,
                    COUNT(*) FILTER (WHERE u.status::text = 'blocked')::int AS blocked_cnt,
                    COUNT(*) FILTER (WHERE u.status::text = 'pending')::int AS pending_cnt,
                    COUNT(*) FILTER (WHERE u.role::text IN ('buyer', 'both'))::int AS buyer_cnt,
                    COUNT(*) FILTER (WHERE u.role::text IN ('supplier', 'both'))::int AS supplier_cnt
                FROM users u
                {where_search}
                """,
                *params[:search_param_count],
            )
            counts = count_rows[0] if count_rows else None
            offset_params = list(params)
            offset_params.extend([page_size, (page - 1) * page_size])
            rows = await conn.fetch(
                f"""
                SELECT
                    u.id, u.email, u.first_name, u.last_name, u.phone,
                    u.role::text AS role, u.status::text AS status,
                    u.banned_until, u.block_reason,
                    u.created_at, u.updated_at,
                    co.company_id,
                    co.company_title,
                    COALESCE(st.contracts_count, 0) AS contracts_count,
                    COALESCE(st.contracts_sum, 0) AS contracts_sum
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT c.id AS company_id, c.title AS company_title
                    FROM company_users cu
                    JOIN companies c ON c.id = cu.company_id
                    WHERE cu.user_id = u.id
                    ORDER BY
                        CASE WHEN cu.role::text = 'director' THEN 0 ELSE 1 END,
                        cu.id
                    LIMIT 1
                ) co ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*)::int AS contracts_count,
                        COALESCE(SUM(x.agreed_amount), 0)::float AS contracts_sum
                    FROM (
                        SELECT DISTINCT ct.id, ct.agreed_amount
                        FROM actors a
                        JOIN contracts ct
                          ON ct.buyer_actor_id = a.id OR ct.supplier_actor_id = a.id
                        WHERE a.user_id = u.id
                           OR (co.company_id IS NOT NULL AND a.company_id = co.company_id)
                    ) x
                ) st ON TRUE
                {where_filter}
                ORDER BY u.created_at DESC, u.id DESC
                LIMIT ${len(offset_params) - 1} OFFSET ${len(offset_params)}
                """,
                *offset_params,
            )

        return {
            "items": [_serialize_list_item(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size),
            "status_counts": {
                "all": int(counts["all_cnt"]) if counts else 0,
                "active": int(counts["active_cnt"]) if counts else 0,
                "blocked": int(counts["blocked_cnt"]) if counts else 0,
                "pending": int(counts["pending_cnt"]) if counts else 0,
            },
            "tab_counts": {
                "all": int(counts["all_cnt"]) if counts else 0,
                "buyer": int(counts["buyer_cnt"]) if counts else 0,
                "supplier": int(counts["supplier_cnt"]) if counts else 0,
                "blocked": int(counts["blocked_cnt"]) if counts else 0,
            },
        }

    async def get_user(self, user_id: int) -> dict:
        async with self.connection() as conn:
            user = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
                user_id,
            )
            if not user:
                raise NotFoundError("User not found")

            company_rows = await conn.fetch(
                """
                SELECT
                    c.id, c.title,
                    c.verification_status::text AS verification_status,
                    c.rating,
                    COALESCE(c.vip, FALSE) AS vip,
                    c.vip_until,
                    (
                        SELECT COUNT(*)::int
                        FROM reviews r
                        JOIN actors a ON a.id = r.target_actor_id
                        WHERE a.company_id = c.id
                    ) AS reviews_count
                FROM company_users cu
                JOIN companies c ON c.id = cu.company_id
                WHERE cu.user_id = $1
                ORDER BY
                    CASE WHEN cu.role::text = 'director' THEN 0 ELSE 1 END,
                    cu.id
                """,
                user_id,
            )
            company_ids = [row["id"] for row in company_rows]
            primary = company_rows[0] if company_rows else None

            actor_rows = await conn.fetch(
                """
                SELECT id, side::text AS side, company_id
                FROM actors
                WHERE user_id = $1
                   OR (cardinality($2::int[]) > 0 AND company_id = ANY($2::int[]))
                """,
                user_id,
                company_ids or [],
            )
            actor_ids = [row["id"] for row in actor_rows]

            purchases_count = 0
            sales_count = 0
            contracts_sum = 0.0
            disputes_count = 0
            contracts: list[dict] = []

            if actor_ids:
                stats_row = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) FILTER (
                            WHERE c.buyer_actor_id = ANY($1::int[])
                        )::int AS purchases_count,
                        COUNT(*) FILTER (
                            WHERE c.supplier_actor_id = ANY($1::int[])
                        )::int AS sales_count,
                        COALESCE(SUM(c.agreed_amount), 0)::float AS contracts_sum
                    FROM contracts c
                    WHERE c.buyer_actor_id = ANY($1::int[])
                       OR c.supplier_actor_id = ANY($1::int[])
                    """,
                    actor_ids,
                )
                purchases_count = int(stats_row["purchases_count"] or 0)
                sales_count = int(stats_row["sales_count"] or 0)
                contracts_sum = float(stats_row["contracts_sum"] or 0)

                disputes_count = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM disputes d
                        JOIN contracts c ON c.id = d.contract_id
                        WHERE c.buyer_actor_id = ANY($1::int[])
                           OR c.supplier_actor_id = ANY($1::int[])
                        """,
                        actor_ids,
                    )
                    or 0
                )

                contract_rows = await conn.fetch(
                    """
                    SELECT
                        c.id, c.title, c.status::text AS status,
                        c.agreed_amount, c.currency::text AS currency,
                        c.buyer_actor_id, c.supplier_actor_id, c.created_at,
                        COALESCE(bc.title, ba.display_name, 'Покупатель') AS buyer_name,
                        ba.company_id AS buyer_company_id,
                        COALESCE(sc.title, sa.display_name, 'Поставщик') AS supplier_name,
                        sa.company_id AS supplier_company_id,
                        EXISTS (
                            SELECT 1 FROM disputes d
                            WHERE d.contract_id = c.id
                              AND d.status::text IN ('open', 'under_review', 'appealed')
                        ) AS has_dispute
                    FROM contracts c
                    JOIN actors ba ON ba.id = c.buyer_actor_id
                    JOIN actors sa ON sa.id = c.supplier_actor_id
                    LEFT JOIN companies bc ON bc.id = ba.company_id
                    LEFT JOIN companies sc ON sc.id = sa.company_id
                    WHERE c.buyer_actor_id = ANY($1::int[])
                       OR c.supplier_actor_id = ANY($1::int[])
                    ORDER BY c.created_at DESC
                    LIMIT 50
                    """,
                    actor_ids,
                )
                actor_id_set = set(actor_ids)
                for row in contract_rows:
                    is_buyer = row["buyer_actor_id"] in actor_id_set
                    contracts.append(
                        {
                            "id": row["id"],
                            "title": row["title"],
                            "status": row["status"],
                            "agreed_amount": float(row["agreed_amount"] or 0),
                            "currency": row["currency"],
                            "role": "buyer" if is_buyer else "supplier",
                            "counterparty": (
                                row["supplier_name"] if is_buyer else row["buyer_name"]
                            ),
                            "counterparty_company_id": (
                                row["supplier_company_id"]
                                if is_buyer
                                else row["buyer_company_id"]
                            ),
                            "created_at": row["created_at"],
                            "has_dispute": bool(row["has_dispute"]),
                        }
                    )

            chat_rows = []
            if company_ids:
                chat_rows = await conn.fetch(
                    """
                    SELECT * FROM (
                        SELECT
                            'contract'::text AS kind,
                            conv.id AS thread_id,
                            COALESCE(c.title, 'Сделка') AS about,
                            ('БМ-' || c.id::text) AS ref_label,
                            COALESCE(bc.title, ba.display_name, 'Покупатель') AS party_a,
                            COALESCE(sc.title, sa.display_name, 'Поставщик') AS party_b,
                            lm.text AS last_text,
                            lm.created_at AS last_at,
                            EXISTS (
                                SELECT 1 FROM messages mx
                                WHERE mx.conversation_id = conv.id AND mx.text ~* $2
                            ) AS flagged
                        FROM conversations conv
                        JOIN contracts c ON c.id = conv.contract_id
                        JOIN actors ba ON ba.id = c.buyer_actor_id
                        JOIN actors sa ON sa.id = c.supplier_actor_id
                        LEFT JOIN companies bc ON bc.id = ba.company_id
                        LEFT JOIN companies sc ON sc.id = sa.company_id
                        JOIN LATERAL (
                            SELECT m.text, m.created_at
                            FROM messages m
                            WHERE m.conversation_id = conv.id
                            ORDER BY m.created_at DESC
                            LIMIT 1
                        ) lm ON TRUE
                        WHERE ba.company_id = ANY($1::int[])
                           OR sa.company_id = ANY($1::int[])

                        UNION ALL

                        SELECT
                            'proposal'::text AS kind,
                            p.id AS thread_id,
                            COALESCE(r.title, 'Заявка') AS about,
                            COALESCE(r.id::text, 'З-' || p.id::text) AS ref_label,
                            COALESCE(bc.title, ba.display_name, 'Покупатель') AS party_a,
                            COALESCE(sc.title, sa.display_name, 'Поставщик') AS party_b,
                            lm.text AS last_text,
                            lm.created_at AS last_at,
                            EXISTS (
                                SELECT 1 FROM proposal_messages mx
                                WHERE mx.proposal_id = p.id AND mx.text ~* $2
                            ) AS flagged
                        FROM proposals p
                        JOIN rfqs r ON r.id = p.rfq_id
                        JOIN actors ba ON ba.id = r.actor_id
                        JOIN actors sa ON sa.id = p.supplier_actor_id
                        LEFT JOIN companies bc ON bc.id = ba.company_id
                        LEFT JOIN companies sc ON sc.id = sa.company_id
                        JOIN LATERAL (
                            SELECT m.text, m.created_at
                            FROM proposal_messages m
                            WHERE m.proposal_id = p.id
                            ORDER BY m.created_at DESC
                            LIMIT 1
                        ) lm ON TRUE
                        WHERE ba.company_id = ANY($1::int[])
                           OR sa.company_id = ANY($1::int[])
                    ) chats
                    ORDER BY last_at DESC NULLS LAST
                    LIMIT 50
                    """,
                    company_ids,
                    CHAT_RISK_RE.pattern,
                )

            chats = [
                {
                    "id": f"{row['kind']}:{row['thread_id']}",
                    "kind": row["kind"],
                    "about": row["about"],
                    "ref_label": row["ref_label"],
                    "party_a": row["party_a"],
                    "party_b": row["party_b"],
                    "last_text": row["last_text"] or "",
                    "last_at": row["last_at"],
                    "flagged": bool(row["flagged"]),
                }
                for row in chat_rows
            ]

            history_rows = await conn.fetch(
                """
                SELECT
                    a.id, a.action, a.details, a.created_at,
                    u.id AS user_id, u.email, u.first_name, u.last_name
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE (a.resource_type = 'user' AND a.resource_id = $1)
                   OR (
                        a.user_id = $2
                        AND a.action NOT LIKE 'http.%'
                        AND a.resource_type <> 'http'
                   )
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT 50
                """,
                str(user_id),
                user_id,
            )
            history = [serialize_audit_entry(row) for row in history_rows]

            documents: list[dict] = []
            verification_checklist: dict[str, bool] = {}
            if primary:
                certificates = await conn.fetch(
                    """
                    SELECT id, title, issuer, file_url
                    FROM company_certificates
                    WHERE company_id = $1
                    ORDER BY id
                    """,
                    primary["id"],
                )
                company_full = await conn.fetchrow(
                    """
                    SELECT legal_name, tax_number, address, website
                    FROM companies WHERE id = $1
                    """,
                    primary["id"],
                )
                documents = [
                    {
                        "id": cert["id"],
                        "title": cert["title"] or cert["issuer"] or f"Документ {cert['id']}",
                        "file_url": file_url(cert["file_url"]),
                        "kind": "certificate",
                    }
                    for cert in certificates
                ]
                verification_checklist = {
                    "legal_name": bool(company_full and company_full["legal_name"]),
                    "tax_number": bool(company_full and company_full["tax_number"]),
                    "address": bool(company_full and company_full["address"]),
                    "website": bool(company_full and company_full["website"]),
                    "certificates": bool(certificates),
                }

        return {
            "id": user["id"],
            "email": user["email"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "phone": user["phone"],
            "role": user["role"],
            "status": user["status"],
            "banned_until": user.get("banned_until"),
            "block_reason": user.get("block_reason"),
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
            "companies": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "verification_status": row["verification_status"],
                    "rating": float(row["rating"] or 0),
                    "reviews_count": int(row["reviews_count"] or 0),
                    "vip": bool(row["vip"]),
                    "vip_until": row["vip_until"],
                }
                for row in company_rows
            ],
            "stats": {
                "purchases_count": purchases_count,
                "sales_count": sales_count,
                "contracts_sum": contracts_sum,
                "disputes_count": disputes_count,
                "rating": float(primary["rating"]) if primary else None,
                "reviews_count": int(primary["reviews_count"] or 0) if primary else 0,
                "chats_count": len(chats),
                "flagged_chats_count": sum(1 for chat in chats if chat["flagged"]),
            },
            "contracts": contracts,
            "history": history,
            "chats": chats,
            "documents": documents,
            "verification_checklist": verification_checklist,
        }

    async def update_user_status(
        self,
        user_id: int,
        status: str,
        current_user,
        reason: str | None = None,
        *,
        duration_days: int | None = None,
        permanent: bool = False,
    ):
        from datetime import datetime, timedelta, timezone

        ensure_permission(current_user, "admin_users.update")
        status_value = status.value if hasattr(status, "value") else status
        async with self.connection() as conn:
            user = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
                user_id,
            )
            if not user:
                raise NotFoundError("User not found")
            if user["id"] == actor_id_of(current_user):
                raise ForbiddenError("You cannot change your own account status")
            current_rank = STAFF_RANK.get(actor_role_of(current_user), 0)
            target_rank = STAFF_RANK.get(user["role"], 0)
            if target_rank and target_rank >= current_rank:
                raise ForbiddenError("You cannot change this staff account status")
            previous_status = user["status"]
            banned_until = None
            block_reason = reason.strip() if reason and reason.strip() else None
            bump_sessions = False
            if status_value == "blocked":
                bump_sessions = True
                if permanent or not duration_days:
                    banned_until = None
                else:
                    banned_until = datetime.now(timezone.utc) + timedelta(days=int(duration_days))
            elif status_value == "active":
                banned_until = None
                block_reason = None
            updated = await conn.fetchrow(
                f"""
                UPDATE users
                SET status = $1::user_status,
                    banned_until = $2,
                    block_reason = $3,
                    token_version = CASE WHEN $4 THEN COALESCE(token_version, 0) + 1 ELSE token_version END,
                    updated_at = NOW()
                WHERE id = $5
                RETURNING {USER_COLUMNS}
                """,
                status_value,
                banned_until,
                block_reason,
                bump_sessions,
                user_id,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.user.status.{status_value}",
                "user",
                user_id,
                {
                    "previous_status": previous_status,
                    "status": status_value,
                    "reason": block_reason,
                    "banned_until": banned_until.isoformat() if banned_until else None,
                    "duration_days": duration_days if status_value == "blocked" and not permanent else None,
                    "permanent": permanent if status_value == "blocked" else None,
                },
            )
        return {
            "id": updated["id"],
            "email": updated["email"],
            "first_name": updated["first_name"],
            "last_name": updated["last_name"],
            "phone": updated["phone"],
            "role": updated["role"],
            "status": updated["status"],
            "banned_until": updated.get("banned_until"),
            "block_reason": updated.get("block_reason"),
            "created_at": updated["created_at"],
            "updated_at": updated["updated_at"],
        }

    async def revoke_user_sessions(self, user_id: int, current_user) -> dict:
        ensure_permission(current_user, "admin_users.update")
        async with self.connection() as conn:
            user = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
                user_id,
            )
            if not user:
                raise NotFoundError("User not found")
            if user["id"] == actor_id_of(current_user):
                raise ForbiddenError("You cannot revoke your own sessions here")
            current_rank = STAFF_RANK.get(actor_role_of(current_user), 0)
            target_rank = STAFF_RANK.get(user["role"], 0)
            if target_rank and target_rank >= current_rank:
                raise ForbiddenError("You cannot revoke sessions of this staff account")
            updated = await conn.fetchrow(
                f"""
                UPDATE users
                SET token_version = COALESCE(token_version, 0) + 1,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING {USER_COLUMNS}
                """,
                user_id,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                "admin.user.sessions.revoke",
                "user",
                user_id,
                {"token_version": int(updated["token_version"] or 0)},
            )
        return {
            "user_id": updated["id"],
            "token_version": int(updated["token_version"] or 0),
        }
