from app.modules.admin.services.common import (
    ESCROW_AWAITING_STATUSES,
    ESCROW_DISPUTED_STATUSES,
    ESCROW_HELD_STATUSES,
    normalize_query,
    pages_for,
)

ESCROW_VIEW_STATUSES: dict[str, tuple[str, ...]] = {
    "held": ESCROW_HELD_STATUSES,
    "disputed": ESCROW_DISPUTED_STATUSES,
    "awaiting": ESCROW_AWAITING_STATUSES,
}

PARTY_NAME_SQL = "COALESCE(NULLIF(TRIM({company}.title), ''), {actor}.display_name)"


class AdminEscrowMixin:
    async def get_escrow(
        self,
        view: str = "held",
        page: int = 1,
        page_size: int = 20,
        query: str | None = None,
    ) -> dict:
        statuses = ESCROW_VIEW_STATUSES.get(view, ESCROW_HELD_STATUSES)
        normalized = normalize_query(query)
        async with self.connection() as conn:
            summary_rows = await conn.fetch(
                """
                SELECT c.currency::text AS currency,
                       COALESCE(SUM(m.amount) FILTER (WHERE m.status::text = ANY($1::text[])), 0) AS held,
                       COALESCE(SUM(m.amount) FILTER (WHERE m.status::text = ANY($2::text[])), 0) AS disputed,
                       COALESCE(SUM(m.amount) FILTER (WHERE m.status::text = ANY($3::text[])), 0) AS awaiting,
                       COALESCE(SUM(m.commission_amount) FILTER (
                           WHERE m.status::text = ANY($1::text[]) OR m.status::text = ANY($2::text[])
                       ), 0) AS expected_commission
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                GROUP BY c.currency
                ORDER BY c.currency
                """,
                list(ESCROW_HELD_STATUSES),
                list(ESCROW_DISPUTED_STATUSES),
                list(ESCROW_AWAITING_STATUSES),
            )
            view_counts = {}
            for key, view_statuses in ESCROW_VIEW_STATUSES.items():
                view_counts[key] = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(DISTINCT contract_id)
                        FROM payment_milestones
                        WHERE status::text = ANY($1::text[])
                        """,
                        list(view_statuses),
                    )
                )

            params: list = [list(statuses)]
            search_sql = ""
            if normalized:
                params.append(f"%{normalized}%")
                search_sql = f"AND (c.title ILIKE ${len(params)} OR c.id::text = ${len(params) + 1})"
                params.append(normalized.lstrip("#"))
            base_sql = f"""
                FROM (
                    SELECT m.contract_id,
                           SUM(m.amount) AS amount,
                           SUM(m.commission_amount) AS commission,
                           COUNT(*) AS milestones_count
                    FROM payment_milestones m
                    WHERE m.status::text = ANY($1::text[])
                    GROUP BY m.contract_id
                ) agg
                JOIN contracts c ON c.id = agg.contract_id
                LEFT JOIN actors ba ON ba.id = c.buyer_actor_id
                LEFT JOIN companies bc ON bc.id = ba.company_id
                LEFT JOIN actors sa ON sa.id = c.supplier_actor_id
                LEFT JOIN companies sc ON sc.id = sa.company_id
                WHERE TRUE {search_sql}
            """
            total = int(await conn.fetchval(f"SELECT COUNT(*) {base_sql}", *params))
            params.extend([page_size, (page - 1) * page_size])
            rows = await conn.fetch(
                f"""
                SELECT c.id, c.title, c.status::text AS status, c.currency::text AS currency,
                       c.agreed_amount, c.created_at,
                       agg.amount, agg.commission, agg.milestones_count,
                       {PARTY_NAME_SQL.format(company="bc", actor="ba")} AS buyer_name,
                       {PARTY_NAME_SQL.format(company="sc", actor="sa")} AS supplier_name
                {base_sql}
                ORDER BY agg.amount DESC, c.id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )

        return {
            "summary": [
                {
                    "currency": row["currency"],
                    "held": float(row["held"]),
                    "disputed": float(row["disputed"]),
                    "awaiting": float(row["awaiting"]),
                    "expected_commission": float(row["expected_commission"]),
                }
                for row in summary_rows
                if row["held"] or row["disputed"] or row["awaiting"]
            ],
            "view_counts": view_counts,
            "items": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "currency": row["currency"],
                    "agreed_amount": float(row["agreed_amount"]),
                    "amount": float(row["amount"]),
                    "commission": float(row["commission"] or 0),
                    "milestones_count": int(row["milestones_count"]),
                    "buyer_name": row["buyer_name"],
                    "supplier_name": row["supplier_name"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size),
        }
