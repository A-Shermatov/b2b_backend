from datetime import timedelta

from app.modules.settings.service import SETTINGS_CURRENCY

ANALYTICS_PERIODS: dict[str, tuple[str, timedelta]] = {
    "30d": ("day", timedelta(days=29)),
    "90d": ("week", timedelta(weeks=12)),
    "12m": ("month", timedelta(days=334)),
}

COUNT_SERIES: dict[str, str] = {
    "users": "SELECT created_at AS ts, 1 AS value FROM users",
    "companies": "SELECT created_at AS ts, 1 AS value FROM companies",
    "rfqs": "SELECT created_at AS ts, 1 AS value FROM rfqs",
    "contracts": "SELECT created_at AS ts, 1 AS value FROM contracts",
}

AMOUNT_SERIES: dict[str, str] = {
    "contract_volume": (
        "SELECT created_at AS ts, agreed_amount AS value FROM contracts "
        "WHERE currency::text = $3"
    ),
    "released": (
        "SELECT paid_at AS ts, amount AS value FROM supplier_invoices "
        "WHERE status::text = 'paid' AND paid_at IS NOT NULL AND currency::text = $3"
    ),
    "commission": (
        "SELECT paid_at AS ts, amount AS value FROM platform_payments "
        "WHERE type::text = 'commission' AND status::text = 'paid' "
        "AND paid_at IS NOT NULL AND currency::text = $3"
    ),
}


class AdminAnalyticsMixin:
    async def get_analytics(self, period: str = "30d") -> dict:
        unit, span = ANALYTICS_PERIODS.get(period, ANALYTICS_PERIODS["30d"])
        currency = SETTINGS_CURRENCY
        async with self.connection() as conn:
            bounds = await conn.fetchrow(
                """
                WITH b AS (
                    SELECT date_trunc($1, NOW() - $2::interval) AS start_at,
                           date_trunc($1, NOW()) + ('1 ' || $1)::interval AS end_at
                )
                SELECT start_at, start_at - (end_at - start_at) AS previous_start_at
                FROM b
                """,
                unit,
                span,
            )
            bucket_rows = await conn.fetch(
                """
                SELECT generate_series($2::timestamptz, date_trunc($1, NOW()), ('1 ' || $1)::interval) AS bucket
                """,
                unit,
                bounds["start_at"],
            )
            buckets = [row["bucket"] for row in bucket_rows]
            series: dict[str, dict] = {key: {} for key in (*COUNT_SERIES, *AMOUNT_SERIES)}
            totals: dict[str, float] = {}
            previous: dict[str, float] = {}

            for key, source in {**COUNT_SERIES, **AMOUNT_SERIES}.items():
                params = [unit, bounds["start_at"]]
                if key in AMOUNT_SERIES:
                    params.append(currency)
                rows = await conn.fetch(
                    f"""
                    SELECT date_trunc($1, src.ts) AS bucket, COALESCE(SUM(src.value), 0) AS value
                    FROM ({source}) src
                    WHERE src.ts >= $2
                    GROUP BY 1
                    """,
                    *params,
                )
                series[key] = {row["bucket"]: float(row["value"]) for row in rows}
                totals[key] = sum(series[key].values())
                previous_params = [bounds["previous_start_at"], bounds["start_at"]]
                if key in AMOUNT_SERIES:
                    previous_params.append(currency)
                previous[key] = float(
                    await conn.fetchval(
                        f"""
                        SELECT COALESCE(SUM(src.value), 0)
                        FROM ({source}) src
                        WHERE src.ts >= $1 AND src.ts < $2
                        """,
                        *previous_params,
                    )
                )

        return {
            "period": period if period in ANALYTICS_PERIODS else "30d",
            "bucket": unit,
            "currency": currency,
            "totals": totals,
            "previous_totals": previous,
            "series": [
                {
                    "bucket": bucket,
                    **{key: values.get(bucket, 0.0) for key, values in series.items()},
                }
                for bucket in buckets
            ],
        }
