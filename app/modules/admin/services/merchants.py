from app.config import get_settings
from app.core.pagination import build_page, fetch_page
from app.modules.admin.services.base import AdminServiceBase


def _mask_terminal(terminal_id: str | None) -> str | None:
    if not terminal_id:
        return None
    value = str(terminal_id)
    if len(value) <= 4:
        return value
    return f"{'•' * max(0, len(value) - 4)}{value[-4:]}"


def _merchant_card(
    *,
    role: str,
    label: str,
    terminal_id: str | None,
    held: float,
    released: float,
    refunded: float,
    currency: str,
) -> dict:
    return {
        "role": role,
        "label": label,
        "terminal_id": terminal_id,
        "terminal_masked": _mask_terminal(terminal_id),
        "held": round(held, 2),
        "released": round(released, 2),
        "refunded": round(refunded, 2),
        "net_released": round(max(0.0, released - refunded), 2),
        "currency": currency,
        "configured": bool(terminal_id),
    }


class AdminMerchantsMixin(AdminServiceBase):
    async def get_merchant_balances(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        query: str | None = None,
    ) -> dict:
        """Ledger balances for Alif marketplace merchants + per-supplier wallets.

        Alif does not expose a live terminal-balance API, so totals are computed
        from payment_milestones / withdrawals on our side.
        """
        settings = get_settings()
        currency = settings.alif_currency or "TJS"
        supplier_terminal = settings.alif_test_merchant_1
        platform_terminal = settings.alif_test_merchant_2

        async with self.connection() as conn:
            totals = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(
                        CASE WHEN m.status::text IN ('funded', 'disputed')
                             THEN m.amount - COALESCE(m.commission_amount, 0)
                             ELSE 0 END
                    ), 0) AS supplier_held,
                    COALESCE(SUM(
                        CASE WHEN m.status::text IN ('funded', 'disputed')
                             THEN COALESCE(m.commission_amount, 0)
                             ELSE 0 END
                    ), 0) AS platform_held,
                    COALESCE(SUM(
                        CASE WHEN m.status::text = 'released'
                             THEN m.amount - COALESCE(m.commission_amount, 0)
                             ELSE 0 END
                    ), 0) AS supplier_released,
                    COALESCE(SUM(
                        CASE WHEN m.status::text = 'released'
                             THEN COALESCE(m.commission_amount, 0)
                             ELSE 0 END
                    ), 0) AS platform_released,
                    COALESCE(SUM(
                        CASE WHEN m.status::text = 'refunded'
                             THEN m.amount - COALESCE(m.commission_amount, 0)
                             ELSE 0 END
                    ), 0) AS supplier_refunded,
                    COALESCE(SUM(
                        CASE WHEN m.status::text = 'refunded'
                             THEN COALESCE(m.commission_amount, 0)
                             ELSE 0 END
                    ), 0) AS platform_refunded
                FROM payment_milestones m
                """
            )
            withdrawn_total = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM withdrawals
                WHERE status::text = ANY($1::text[])
                """,
                ["pending", "processing", "completed"],
            )

            filters = ["a.side::text = 'supplier'", "a.is_active = TRUE"]
            params: list = []
            normalized = (query or "").strip()
            if normalized:
                params.append(f"%{normalized}%")
                idx = len(params)
                filters.append(
                    f"""
                    (
                        a.display_name ILIKE ${idx}
                        OR c.title ILIKE ${idx}
                        OR u.email ILIKE ${idx}
                        OR a.id::text ILIKE ${idx}
                    )
                    """
                )
            where = " AND ".join(filters)
            base_sql = f"""
                SELECT
                    a.id AS actor_id,
                    a.display_name,
                    a.company_id,
                    c.title AS company_title,
                    u.email AS owner_email,
                    COALESCE(ms.released, 0) AS released,
                    COALESCE(ms.pending, 0) AS pending,
                    COALESCE(wd.withdrawn, 0) AS withdrawn,
                    COALESCE(ms.currency, ${len(params) + 1}) AS currency
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                LEFT JOIN users u ON u.id = COALESCE(c.owner_id, a.user_id)
                LEFT JOIN LATERAL (
                    SELECT
                        SUM(
                            CASE WHEN m.status::text = 'released'
                                 THEN m.amount - COALESCE(m.commission_amount, 0)
                                 ELSE 0 END
                        ) AS released,
                        SUM(
                            CASE WHEN m.status::text IN ('funded', 'disputed')
                                 THEN m.amount - COALESCE(m.commission_amount, 0)
                                 ELSE 0 END
                        ) AS pending,
                        MAX(ct.currency::text) AS currency
                    FROM contracts ct
                    JOIN payment_milestones m ON m.contract_id = ct.id
                    WHERE ct.supplier_actor_id = a.id
                      AND m.status::text IN ('released', 'funded', 'disputed')
                ) ms ON TRUE
                LEFT JOIN LATERAL (
                    SELECT SUM(w.amount) AS withdrawn
                    FROM withdrawals w
                    WHERE w.actor_id = a.id
                      AND w.status::text = ANY(
                          ARRAY['pending', 'processing', 'completed']::text[]
                      )
                ) wd ON TRUE
                WHERE {where}
            """
            list_params = [*params, currency]
            count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS listed"
            page_sql = f"{base_sql} ORDER BY pending DESC, released DESC, a.id DESC"
            total, rows = await fetch_page(
                conn, count_sql, page_sql, list_params, page, page_size
            )

        supplier_held = float(totals["supplier_held"] or 0)
        platform_held = float(totals["platform_held"] or 0)
        supplier_released = float(totals["supplier_released"] or 0)
        platform_released = float(totals["platform_released"] or 0)
        supplier_refunded = float(totals["supplier_refunded"] or 0)
        platform_refunded = float(totals["platform_refunded"] or 0)
        withdrawn = float(withdrawn_total or 0)

        merchants = [
            _merchant_card(
                role="supplier_pool",
                label="Мерчант исполнителей",
                terminal_id=supplier_terminal,
                held=supplier_held,
                released=supplier_released,
                refunded=supplier_refunded,
                currency=currency,
            ),
            _merchant_card(
                role="platform",
                label="Мерчант площадки (комиссия)",
                terminal_id=platform_terminal,
                held=platform_held,
                released=platform_released,
                refunded=platform_refunded,
                currency=currency,
            ),
        ]

        supplier_items = []
        for row in rows:
            released = float(row["released"] or 0)
            pending = float(row["pending"] or 0)
            withdrawn_amt = float(row["withdrawn"] or 0)
            available = max(0.0, released - withdrawn_amt)
            supplier_items.append(
                {
                    "actor_id": row["actor_id"],
                    "display_name": row["display_name"],
                    "company_id": row["company_id"],
                    "company_title": row["company_title"],
                    "owner_email": row["owner_email"],
                    "available": round(available, 2),
                    "pending": round(pending, 2),
                    "escrow_locked": round(pending, 2),
                    "released": round(released, 2),
                    "withdrawn": round(withdrawn_amt, 2),
                    "currency": row["currency"] or currency,
                }
            )

        return {
            "source": "ledger",
            "note": (
                "Суммы по данным платформы (этапы сделок и выводы). "
                "Alif не отдаёт живой баланс терминала через API."
            ),
            "gateway_configured": bool(
                settings.alif_terminal_key and settings.alif_terminal_password
            ),
            "marketplace_enabled": settings.alif_marketplace_enabled,
            "currency": currency,
            "merchants": merchants,
            "totals": {
                "held": round(supplier_held + platform_held, 2),
                "released": round(supplier_released + platform_released, 2),
                "refunded": round(supplier_refunded + platform_refunded, 2),
                "supplier_available": round(max(0.0, supplier_released - withdrawn), 2),
                "withdrawn": round(withdrawn, 2),
                "currency": currency,
            },
            "suppliers": build_page(supplier_items, total, page, page_size),
        }
