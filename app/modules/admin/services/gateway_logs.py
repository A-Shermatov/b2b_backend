from datetime import datetime, timedelta, timezone

from app.core.exceptions import NotFoundError
from app.modules.admin.schemas.gateway_logs import AdminGatewayLogDetail, AdminGatewayLogItem
from app.modules.admin.services.common import normalize_query, pages_for, parse_json

PERIOD_INTERVALS = {"24h": timedelta(days=1), "7d": timedelta(days=7), "30d": timedelta(days=30)}

ITEM_COLUMNS = """
    gl.id, gl.created_at, gl.responded_at, gl.gateway, gl.direction, gl.operation,
    gl.order_id, gl.method, gl.url, gl.response_status, gl.error
"""

TXN_COLUMNS = """
    pgt.external_transaction_id,
    pgt.status AS gateway_txn_status,
    pgt.amount,
    pgt.currency,
    pgt.paid_at,
    pgt.milestone_id,
    pgt.contract_id
"""

EXPORT_LIMIT = 500


def _json_field(value):
    if value is None:
        return None
    return parse_json(value)


def _payment_kind(data: dict) -> str:
    if data.get("milestone_id") is not None:
        return "milestone"
    if data.get("subscription_user_id") is not None:
        return "subscription"
    return "unknown"


class AdminGatewayLogsMixin:
    def _gateway_log_filters(
        self,
        *,
        order_id: str | None,
        gateway: str | None,
        operation: str | None,
        direction: str | None,
        period: str,
        query: str | None,
    ) -> tuple[list, str]:
        params: list = []
        clauses: list[str] = []
        if period in PERIOD_INTERVALS:
            params.append(PERIOD_INTERVALS[period])
            clauses.append(f"gl.created_at >= NOW() - ${len(params)}::interval")
        normalized_order = normalize_query(order_id)
        if normalized_order:
            params.append(f"%{normalized_order}%")
            clauses.append(f"gl.order_id ILIKE ${len(params)}")
        if gateway:
            params.append(gateway)
            clauses.append(f"gl.gateway = ${len(params)}")
        if operation:
            params.append(operation)
            clauses.append(f"gl.operation = ${len(params)}")
        if direction:
            params.append(direction)
            clauses.append(f"gl.direction = ${len(params)}")
        normalized = normalize_query(query)
        if normalized:
            params.append(f"%{normalized}%")
            placeholder = f"${len(params)}"
            clauses.append(
                f"""(
                    gl.order_id ILIKE {placeholder}
                    OR gl.url ILIKE {placeholder}
                    OR gl.error ILIKE {placeholder}
                    OR EXISTS (
                        SELECT 1 FROM payment_gateway_transactions pgt_q
                        WHERE pgt_q.order_id = gl.order_id
                          AND pgt_q.external_transaction_id ILIKE {placeholder}
                    )
                )"""
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return params, where

    async def list_gateway_logs(
        self,
        order_id: str | None = None,
        gateway: str | None = None,
        operation: str | None = None,
        direction: str | None = None,
        period: str = "7d",
        query: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        params, where = self._gateway_log_filters(
            order_id=order_id,
            gateway=gateway,
            operation=operation,
            direction=direction,
            period=period,
            query=query,
        )

        async with self.connection() as conn:
            total = int(
                await conn.fetchval(
                    f"SELECT COUNT(*) FROM payment_gateway_http_logs gl {where}",
                    *params,
                )
            )
            list_params = [*params, page_size, (page - 1) * page_size]
            rows = await conn.fetch(
                f"""
                SELECT {ITEM_COLUMNS},
                    pgt.external_transaction_id,
                    pgt.amount,
                    pgt.currency
                FROM payment_gateway_http_logs gl
                LEFT JOIN payment_gateway_transactions pgt
                    ON gl.order_id IS NOT NULL AND pgt.order_id = gl.order_id
                {where}
                ORDER BY gl.created_at DESC, gl.id DESC
                LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}
                """,
                *list_params,
            )

        items = []
        for row in rows:
            data = dict(row)
            if data.get("amount") is not None:
                data["amount"] = float(data["amount"])
            items.append(AdminGatewayLogItem.model_validate(data))

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size),
        }

    async def get_gateway_log(self, log_id: int) -> AdminGatewayLogDetail:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {ITEM_COLUMNS},
                    gl.request_headers, gl.request_body, gl.response_body, gl.response_raw,
                    {TXN_COLUMNS},
                    spo.user_id AS subscription_user_id, spo.plan
                FROM payment_gateway_http_logs gl
                LEFT JOIN payment_gateway_transactions pgt
                    ON gl.order_id IS NOT NULL AND pgt.order_id = gl.order_id
                LEFT JOIN subscription_payment_orders spo
                    ON gl.order_id LIKE 'SUB%' AND spo.order_id = gl.order_id
                WHERE gl.id = $1
                """,
                log_id,
            )
        if not row:
            raise NotFoundError("Gateway log not found")
        data = dict(row)
        for key in ("request_headers", "request_body", "response_body"):
            data[key] = _json_field(data[key])
        if data.get("amount") is not None:
            data["amount"] = float(data["amount"])
        return AdminGatewayLogDetail.model_validate(
            {**data, "payment_kind": _payment_kind(data)}
        )

    async def export_gateway_logs(
        self,
        order_id: str | None = None,
        gateway: str | None = None,
        operation: str | None = None,
        direction: str | None = None,
        period: str = "7d",
        query: str | None = None,
    ) -> dict:
        """Full HTTP payloads for sharing with bank developers (capped)."""
        params, where = self._gateway_log_filters(
            order_id=order_id,
            gateway=gateway,
            operation=operation,
            direction=direction,
            period=period,
            query=query,
        )
        async with self.connection() as conn:
            total = int(
                await conn.fetchval(
                    f"SELECT COUNT(*) FROM payment_gateway_http_logs gl {where}",
                    *params,
                )
            )
            list_params = [*params, EXPORT_LIMIT]
            rows = await conn.fetch(
                f"""
                SELECT {ITEM_COLUMNS},
                    gl.request_headers, gl.request_body, gl.response_body, gl.response_raw,
                    {TXN_COLUMNS},
                    spo.user_id AS subscription_user_id, spo.plan
                FROM payment_gateway_http_logs gl
                LEFT JOIN payment_gateway_transactions pgt
                    ON gl.order_id IS NOT NULL AND pgt.order_id = gl.order_id
                LEFT JOIN subscription_payment_orders spo
                    ON gl.order_id LIKE 'SUB%' AND spo.order_id = gl.order_id
                {where}
                ORDER BY gl.created_at DESC, gl.id DESC
                LIMIT ${len(list_params)}
                """,
                *list_params,
            )

        items = []
        for row in rows:
            data = dict(row)
            for key in ("request_headers", "request_body", "response_body"):
                data[key] = _json_field(data[key])
            if data.get("amount") is not None:
                data["amount"] = float(data["amount"])
            items.append(
                AdminGatewayLogDetail.model_validate(
                    {**data, "payment_kind": _payment_kind(data)}
                )
            )

        return {
            "generated_at": datetime.now(timezone.utc),
            "filters": {
                "order_id": order_id,
                "gateway": gateway,
                "operation": operation,
                "direction": direction,
                "period": period,
                "query": query,
                "limit": EXPORT_LIMIT,
            },
            "total": total,
            "items": items,
        }
