from datetime import datetime, timezone


class AdminDashboardMixin:
    async def get_dashboard(self) -> dict:
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        async with self.connection() as conn:
            metrics = {
                "total_users": int(await conn.fetchval("SELECT COUNT(*) FROM users")),
                "total_companies": int(await conn.fetchval("SELECT COUNT(*) FROM companies")),
                "catalog_items": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM catalog_items WHERE status::text != 'deleted'"
                    )
                ),
                "active_rfqs": int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM rfqs
                        WHERE status::text IN (
                            'published', 'receiving_proposals', 'supplier_selected',
                            'contract_created', 'in_progress'
                        )
                        """
                    )
                ),
                "active_contracts": int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM contracts
                        WHERE status::text IN ('pending_payment', 'active', 'delivered')
                        """
                    )
                ),
                "escrow_balance": float(
                    await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(amount), 0.0)
                        FROM payment_milestones
                        WHERE status::text IN (
                            'funded', 'in_progress', 'submitted', 'approved', 'disputed'
                        )
                        """
                    )
                ),
                "open_disputes": int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM disputes
                        WHERE status::text IN ('open', 'under_review', 'appealed')
                        """
                    )
                ),
                "monthly_revenue": float(
                    await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(amount), 0.0)
                        FROM supplier_invoices
                        WHERE status::text = 'paid' AND paid_at >= $1
                        """,
                        month_start,
                    )
                ),
                "pending_verifications": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM companies WHERE verification_status::text = 'pending'"
                    )
                ),
            }
            recent_users = await conn.fetch(
                """
                SELECT id, email, first_name, last_name, created_at
                FROM users ORDER BY created_at DESC LIMIT 5
                """
            )
            recent_contracts = await conn.fetch(
                """
                SELECT id, title, created_at
                FROM contracts ORDER BY created_at DESC LIMIT 5
                """
            )
            recent_payments = await conn.fetch(
                """
                SELECT id, title, amount, currency, paid_at, issued_at
                FROM supplier_invoices
                WHERE status::text = 'paid'
                ORDER BY paid_at DESC NULLS LAST
                LIMIT 5
                """
            )
            recent_disputes = await conn.fetch(
                """
                SELECT d.id, d.contract_id, d.created_at, c.title AS contract_title
                FROM disputes d
                LEFT JOIN contracts c ON c.id = d.contract_id
                WHERE d.status::text IN ('open', 'under_review', 'appealed')
                ORDER BY d.created_at DESC
                LIMIT 5
                """
            )

        activity = [
            {
                "id": f"user-{user['id']}",
                "type": "registration",
                "title": f"{user['first_name']} {user['last_name']}".strip() or user["email"],
                "description": user["email"],
                "happened_at": user["created_at"],
            }
            for user in recent_users
        ]
        activity.extend(
            {
                "id": f"contract-{contract['id']}",
                "type": "contract",
                "title": contract["title"],
                "description": f"Контракт #{contract['id']}",
                "happened_at": contract["created_at"],
            }
            for contract in recent_contracts
        )
        activity.extend(
            {
                "id": f"payment-{invoice['id']}",
                "type": "payment",
                "title": invoice["title"],
                "description": f"{invoice['amount']:g} {invoice['currency']}",
                "happened_at": invoice["paid_at"] or invoice["issued_at"],
            }
            for invoice in recent_payments
        )
        activity.extend(
            {
                "id": f"dispute-{dispute['id']}",
                "type": "dispute",
                "title": dispute["contract_title"] or f"Спор #{dispute['id']}",
                "description": f"Спор #{dispute['id']} · контракт #{dispute['contract_id']}",
                "happened_at": dispute["created_at"],
            }
            for dispute in recent_disputes
        )
        activity.sort(key=lambda item: item["happened_at"], reverse=True)
        return {"metrics": metrics, "recent_activity": activity[:12]}
