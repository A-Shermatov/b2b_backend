import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import build_page, fetch_page
from app.database import pool
from app.modules.approvals.schemas import (
    CartCheckoutItem,
    CartCheckoutResponse,
    PurchaseApprovalSchema,
)
from app.modules.catalog.schemas.offers import CatalogOfferCreate
from app.modules.catalog.services import CatalogItemService
from app.modules.catalog.services.offers import minimum_order, unit_price_for_qty

ROLE_LABELS = {
    "director": "Руководитель",
    "admin": "Администратор",
    "moderator": "Модератор",
    "accountant": "Бухгалтер",
    "viewer": "Наблюдатель",
}

# Roles that may create buy-offers / auto-approve without waiting.
PRIVILEGED_ROLES = frozenset({"director", "admin"})


def _parse_json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        return list(value)
    return []


def _row_to_schema(row) -> PurchaseApprovalSchema:
    first = (row["first_name"] or "").strip()
    last = (row["last_name"] or "").strip()
    who_name = (
        f"{first} {last}".strip()
        or row["email"]
        or f"User #{row['requester_user_id']}"
    )
    rfq_ids = [str(x) for x in _parse_json_list(row.get("rfq_ids"))]
    cart_items = _parse_json_list(row.get("cart_items"))
    rfq_id = row.get("rfq_id") or (rfq_ids[0] if rfq_ids else None)
    return PurchaseApprovalSchema(
        id=row["id"],
        company_id=row["company_id"],
        requester_user_id=row["requester_user_id"],
        who_name=who_name,
        who_role=row["requester_role"] or "",
        limit_label=row["limit_label"] or "—",
        title=row["title"],
        supplier_name=row["supplier_name"] or "",
        amount=float(row["amount"]),
        currency=row["currency"] or "TJS",
        reason=row["reason"] or "",
        status=row["status"],
        rfq_id=rfq_id,
        rfq_ids=rfq_ids,
        cart_items=cart_items,
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        created_at=row["created_at"],
    )


def needs_purchase_approval(*, actor_kind: str, company_role: str | None) -> bool:
    if actor_kind != "company":
        return False
    if not company_role:
        raise ForbiddenError("Company role required")
    return company_role not in PRIVILEGED_ROLES


class ApprovalService:
    def connection(self):
        return pool.connection()

    async def list_for_buyer(
        self,
        *,
        company_id: int | None,
        user_id: int,
        status: str | None,
        page: int,
        page_size: int,
    ) -> dict:
        if not company_id:
            return build_page([], 0, page, page_size)

        params: list = [company_id]
        where = "WHERE pa.company_id = $1"
        if status == "wait":
            where += " AND pa.status::text = 'wait'"
        elif status == "hist":
            where += " AND pa.status::text <> 'wait'"
        elif status:
            where += f" AND pa.status::text = ${len(params) + 1}"
            params.append(status)

        async with self.connection() as conn:
            member = await conn.fetchval(
                """
                SELECT 1 FROM company_users
                WHERE company_id = $1 AND user_id = $2
                """,
                company_id,
                user_id,
            )
            if not member:
                raise ForbiddenError("Not a member of this company")

            total, rows = await fetch_page(
                conn,
                f"SELECT COUNT(*) FROM purchase_approvals pa {where}",
                f"""
                SELECT
                    pa.id, pa.company_id, pa.requester_user_id, pa.title,
                    pa.supplier_name, pa.amount, pa.currency, pa.reason,
                    pa.status::text AS status, pa.limit_label, pa.requester_role,
                    pa.rfq_id, pa.rfq_ids, pa.cart_items, pa.decided_by,
                    pa.decided_at, pa.created_at,
                    u.first_name, u.last_name, u.email
                FROM purchase_approvals pa
                JOIN users u ON u.id = pa.requester_user_id
                {where}
                ORDER BY pa.created_at DESC, pa.id DESC
                """,
                params,
                page,
                page_size,
            )
        return build_page([_row_to_schema(row) for row in rows], total, page, page_size)

    async def _load_priced_lines(
        self, conn, items: list[CartCheckoutItem], payment_mode: str = "escrow"
    ) -> tuple[list[dict], float, str]:
        lines: list[dict] = []
        total = 0.0
        currency = "TJS"
        for entry in items:
            item = await conn.fetchrow(
                """
                SELECT ci.id, ci.title, ci.status::text AS status, ci.actor_id
                FROM catalog_items ci
                WHERE ci.id = $1 AND ci.status::text != 'deleted'
                """,
                entry.item_id,
            )
            if not item:
                raise NotFoundError(f"Catalog item {entry.item_id} not found")
            if item["status"] != "active":
                raise ConflictError(f"Catalog item «{item['title']}» is not available")
            pricing = await conn.fetchrow(
                """
                SELECT pricing_type::text AS pricing_type, currency,
                       fixed_price, hourly_rate, monthly_rate, tiers
                FROM item_pricing WHERE item_id = $1
                """,
                entry.item_id,
            )
            if entry.qty < minimum_order(pricing):
                raise ValidationError(
                    f"«{item['title']}»: минимальный заказ — {minimum_order(pricing)} шт."
                )
            if entry.mode == "quote":
                # The supplier names the price; nothing is counted in the total yet.
                lines.append(
                    {
                        "item_id": entry.item_id,
                        "qty": entry.qty,
                        "title": item["title"],
                        "mode": "quote",
                        "payment_mode": payment_mode,
                        "unit_price": None,
                        "line_total": 0.0,
                        "currency": pricing["currency"] if pricing else currency,
                    }
                )
                continue
            unit = unit_price_for_qty(pricing, entry.qty)
            if unit is None:
                raise ValidationError(
                    f"У позиции «{item['title']}» нет опубликованной цены — запросите цену"
                )
            unit_price, line_currency = unit
            if any(line["mode"] == "buy" and line["currency"] != line_currency for line in lines):
                # One approval carries one amount: never add TJS and USD together.
                raise ValidationError(
                    "В корзине позиции в разных валютах — оформите их отдельными заказами"
                )
            currency = line_currency
            line_total = float(
                (Decimal(str(unit_price)) * entry.qty).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
            # Sum in cents: adding floats drifts (0.1 + 0.2 != 0.3).
            total = float(Decimal(str(total)) + Decimal(str(line_total)))
            lines.append(
                {
                    "item_id": entry.item_id,
                    "qty": entry.qty,
                    "title": item["title"],
                    "mode": "buy",
                    "payment_mode": payment_mode,
                    "unit_price": float(unit_price),
                    "line_total": line_total,
                    "currency": currency,
                }
            )
        return lines, total, currency

    async def _create_offers_for_lines(
        self,
        *,
        lines: list[dict],
        buyer_actor_id: int,
        user_id: int,
    ) -> list[str]:
        rfq_ids: list[str] = []
        catalog = CatalogItemService()
        for line in lines:
            qty = int(line.get("qty") or 1)
            kind = "quote" if line.get("mode") == "quote" else "buy"
            payment_mode = "direct" if line.get("payment_mode") == "direct" else "escrow"
            offer = await catalog.create_offer(
                int(line["item_id"]),
                buyer_actor_id,
                str(user_id),
                CatalogOfferCreate(
                    kind=kind,
                    qty=qty,
                    payment_mode=payment_mode,
                    message="Оформлено через корзину",
                ),
                skip_approval_gate=True,
            )
            rfq_ids.append(offer.rfq_id)
        return rfq_ids

    async def _insert_approval(
        self,
        conn,
        *,
        company_id: int | None,
        user_id: int,
        company_role: str | None,
        title: str,
        supplier_name: str,
        amount: float,
        currency: str,
        reason: str,
        status: str,
        cart_items: list[dict],
        rfq_ids: list[str],
        idempotency_key: str | None,
    ):
        role = company_role or ("director" if not company_id else None)
        if company_id and not role:
            raise ForbiddenError("Company role required")
        requester_role = ROLE_LABELS.get(role or "", role or "")
        now = datetime.now(timezone.utc) if status in {"auto", "approved"} else None
        decided_by = user_id if status in {"auto", "approved"} else None
        row = await conn.fetchrow(
            """
            INSERT INTO purchase_approvals (
                company_id, requester_user_id, title, supplier_name,
                amount, currency, reason, status, limit_label,
                requester_role, rfq_id, rfq_ids, cart_items,
                idempotency_key, decided_by, decided_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8::purchase_approval_status,
                $9, $10, $11, $12::jsonb, $13::jsonb, $14, $15, $16
            )
            RETURNING
                id, company_id, requester_user_id, title, supplier_name,
                amount, currency, reason, status::text AS status,
                limit_label, requester_role, rfq_id, rfq_ids, cart_items,
                decided_by, decided_at, created_at
            """,
            company_id,
            user_id,
            title,
            supplier_name,
            amount,
            currency,
            reason,
            status,
            "—",
            requester_role,
            rfq_ids[0] if rfq_ids else None,
            json.dumps(rfq_ids),
            json.dumps(cart_items),
            idempotency_key,
            decided_by,
            now,
        )
        user = await conn.fetchrow(
            "SELECT first_name, last_name, email FROM users WHERE id = $1",
            user_id,
        )
        return _row_to_schema({**dict(row), **dict(user)})

    async def checkout_cart(
        self,
        *,
        company_id: int | None,
        user_id: int,
        buyer_actor_id: int,
        actor_kind: str,
        company_role: str | None,
        items: list[CartCheckoutItem],
        idempotency_key: str | None,
        payment_mode: str = "escrow",
    ) -> CartCheckoutResponse:
        if actor_kind == "company" and not company_role:
            raise ForbiddenError("Company role required")

        key = (idempotency_key or "").strip() or None
        async with self.connection() as conn:
            if key:
                existing = await conn.fetchrow(
                    """
                    SELECT
                        pa.id, pa.company_id, pa.requester_user_id, pa.title,
                        pa.supplier_name, pa.amount, pa.currency, pa.reason,
                        pa.status::text AS status, pa.limit_label, pa.requester_role,
                        pa.rfq_id, pa.rfq_ids, pa.cart_items, pa.decided_by,
                        pa.decided_at, pa.created_at,
                        u.first_name, u.last_name, u.email
                    FROM purchase_approvals pa
                    JOIN users u ON u.id = pa.requester_user_id
                    WHERE pa.requester_user_id = $1 AND pa.idempotency_key = $2
                    """,
                    user_id,
                    key,
                )
                if existing:
                    approval = _row_to_schema(existing)
                    return CartCheckoutResponse(
                        approval=approval,
                        rfq_ids=approval.rfq_ids,
                        offers_created=approval.status in {"auto", "approved"}
                        and bool(approval.rfq_ids),
                    )

            lines, total, currency = await self._load_priced_lines(
                conn, items, payment_mode
            )

        title = (
            lines[0]["title"]
            if len(lines) == 1
            else f"Корзина: {len(lines)} позиций"
        )
        supplier_name = "Исполнитель" if len(lines) == 1 else f"{len(lines)} позиций"
        wait = needs_purchase_approval(
            actor_kind=actor_kind, company_role=company_role
        )

        if wait:
            async with self.connection() as conn:
                approval = await self._insert_approval(
                    conn,
                    company_id=company_id,
                    user_id=user_id,
                    company_role=company_role,
                    title=title,
                    supplier_name=supplier_name,
                    amount=total,
                    currency=currency,
                    reason="Оформление покупки из корзины — ожидает согласования руководителя",
                    status="wait",
                    cart_items=lines,
                    rfq_ids=[],
                    idempotency_key=key,
                )
            return CartCheckoutResponse(
                approval=approval,
                rfq_ids=[],
                offers_created=False,
            )

        # Privileged / individual: create offers then record auto approval.
        rfq_ids: list[str] = []
        try:
            rfq_ids = await self._create_offers_for_lines(
                lines=lines,
                buyer_actor_id=buyer_actor_id,
                user_id=user_id,
            )
            async with self.connection() as conn:
                approval = await self._insert_approval(
                    conn,
                    company_id=company_id,
                    user_id=user_id,
                    company_role=company_role,
                    title=title,
                    supplier_name=supplier_name,
                    amount=total,
                    currency=currency,
                    reason="Оформление покупки из корзины",
                    status="auto",
                    cart_items=lines,
                    rfq_ids=rfq_ids,
                    idempotency_key=key,
                )
        except Exception:
            if rfq_ids:
                async with self.connection() as conn:
                    await self._cancel_rfqs(conn, rfq_ids, buyer_actor_id)
            raise
        return CartCheckoutResponse(
            approval=approval,
            rfq_ids=rfq_ids,
            offers_created=True,
        )

    async def _company_buyer_actor_id(self, conn, company_id: int) -> int:
        actor_id = await conn.fetchval(
            """
            SELECT id FROM actors
            WHERE company_id = $1 AND side::text = 'buyer' AND is_active = TRUE
            ORDER BY id
            LIMIT 1
            """,
            company_id,
        )
        if not actor_id:
            raise ForbiddenError("Buyer company actor not found")
        return int(actor_id)

    async def _cancel_rfqs(self, conn, rfq_ids: list[str], buyer_actor_id: int) -> None:
        for rfq_id in rfq_ids:
            row = await conn.fetchrow(
                "SELECT id, actor_id FROM rfqs WHERE id = $1",
                rfq_id,
            )
            if not row or row["actor_id"] != buyer_actor_id:
                continue
            await conn.execute(
                """
                UPDATE rfqs
                SET status = 'cancelled'::rfq_status, updated_at = NOW()
                WHERE id = $1 AND status::text NOT IN ('cancelled', 'archived', 'completed')
                """,
                rfq_id,
            )
            await conn.execute(
                """
                UPDATE proposals
                SET status = 'withdrawn'::proposal_status
                WHERE rfq_id = $1
                  AND status::text IN ('pending_supplier', 'submitted', 'viewed', 'shortlisted')
                """,
                rfq_id,
            )

    async def decide(
        self,
        *,
        approval_id: int,
        company_id: int | None,
        user_id: int,
        buyer_actor_id: int,
        company_role: str | None,
        action: str,
    ) -> PurchaseApprovalSchema:
        if action not in {"approved", "rejected"}:
            raise ValidationError("Invalid action")
        # Company-scoped only — never allow personal actors to decide org approvals.
        if company_id is None:
            raise ForbiddenError("Company actor required")
        if company_role not in PRIVILEGED_ROLES:
            raise ForbiddenError("Only director or admin can decide")

        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id, company_id, requester_user_id, title, supplier_name,
                    amount, currency, reason, status::text AS status,
                    limit_label, requester_role, rfq_id, rfq_ids, cart_items,
                    decided_by, decided_at, created_at
                FROM purchase_approvals WHERE id = $1
                """,
                approval_id,
            )
            if not row:
                raise NotFoundError("Approval not found")
            if row["company_id"] is None or row["company_id"] != company_id:
                raise ForbiddenError("Approval belongs to another company")
            if row["status"] != "wait":
                raise ValidationError("Approval is already decided")

            cart_items = _parse_json_list(row["cart_items"])
            rfq_ids = [str(x) for x in _parse_json_list(row["rfq_ids"])]
            owner_actor_id = await self._company_buyer_actor_id(conn, company_id)

        if action == "rejected":
            async with self.connection() as conn:
                async with conn.transaction():
                    if rfq_ids:
                        await self._cancel_rfqs(conn, rfq_ids, owner_actor_id)
                    updated = await conn.fetchrow(
                        """
                        UPDATE purchase_approvals
                        SET status = 'rejected'::purchase_approval_status,
                            decided_by = $1,
                            decided_at = NOW()
                        WHERE id = $2
                        RETURNING
                            id, company_id, requester_user_id, title, supplier_name,
                            amount, currency, reason, status::text AS status,
                            limit_label, requester_role, rfq_id, rfq_ids, cart_items,
                            decided_by, decided_at, created_at
                        """,
                        user_id,
                        approval_id,
                    )
                    user = await conn.fetchrow(
                        "SELECT first_name, last_name, email FROM users WHERE id = $1",
                        updated["requester_user_id"],
                    )
            return _row_to_schema({**dict(updated), **dict(user)})

        # approved — create offers from pending cart_items if not yet created
        if not rfq_ids and not cart_items:
            raise ValidationError("Approval has no cart items to fulfill")

        created_now = False
        if not rfq_ids and cart_items:
            try:
                rfq_ids = await self._create_offers_for_lines(
                    lines=cart_items,
                    buyer_actor_id=owner_actor_id,
                    user_id=int(row["requester_user_id"]),
                )
                created_now = True
            except Exception:
                raise

        try:
            async with self.connection() as conn:
                updated = await conn.fetchrow(
                    """
                    UPDATE purchase_approvals
                    SET status = 'approved'::purchase_approval_status,
                        decided_by = $1,
                        decided_at = NOW(),
                        rfq_ids = $2::jsonb,
                        rfq_id = $3
                    WHERE id = $4
                    RETURNING
                        id, company_id, requester_user_id, title, supplier_name,
                        amount, currency, reason, status::text AS status,
                        limit_label, requester_role, rfq_id, rfq_ids, cart_items,
                        decided_by, decided_at, created_at
                    """,
                    user_id,
                    json.dumps(rfq_ids),
                    rfq_ids[0] if rfq_ids else None,
                    approval_id,
                )
                user = await conn.fetchrow(
                    "SELECT first_name, last_name, email FROM users WHERE id = $1",
                    updated["requester_user_id"],
                )
        except Exception:
            if created_now and rfq_ids:
                async with self.connection() as conn:
                    await self._cancel_rfqs(conn, rfq_ids, owner_actor_id)
            raise
        return _row_to_schema({**dict(updated), **dict(user)})
