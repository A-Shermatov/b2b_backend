import json
import time
from dataclasses import asdict, dataclass

from app.database import pool
from app.utils.audit import AuditLogger

CACHE_TTL_SECONDS = 60.0
SETTINGS_CURRENCY = "TJS"
SETTINGS_KEYS = ("commission_percent", "commission_min", "max_contract_amount")
DEFAULT_COMMISSION_PERCENT = 5.0


@dataclass(frozen=True)
class PlatformSettings:
    commission_percent: float = DEFAULT_COMMISSION_PERCENT
    commission_min: float = 0.0
    max_contract_amount: float | None = None
    currency: str = SETTINGS_CURRENCY


_cache: tuple[float, PlatformSettings] | None = None


def invalidate_platform_settings_cache() -> None:
    global _cache
    _cache = None


def _decode(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _to_float(value, fallback: float | None) -> float | None:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


async def _load(conn) -> PlatformSettings:
    rows = await conn.fetch(
        "SELECT key, value FROM platform_settings WHERE key = ANY($1::text[])",
        list(SETTINGS_KEYS),
    )
    values = {row["key"]: _decode(row["value"]) for row in rows}
    return PlatformSettings(
        commission_percent=_to_float(values.get("commission_percent"), DEFAULT_COMMISSION_PERCENT),
        commission_min=_to_float(values.get("commission_min"), 0.0),
        max_contract_amount=_to_float(values.get("max_contract_amount"), None),
    )


async def get_platform_settings(conn=None, *, use_cache: bool = True) -> PlatformSettings:
    global _cache
    if use_cache and _cache is not None and time.monotonic() - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    async with pool.connection(conn) as active:
        settings = await _load(active)
    _cache = (time.monotonic(), settings)
    return settings


async def get_platform_settings_meta(conn) -> dict:
    row = await conn.fetchrow(
        """
        SELECT ps.updated_at,
               NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), '') AS updated_by_name
        FROM platform_settings ps
        LEFT JOIN users u ON u.id = ps.updated_by
        WHERE ps.key = ANY($1::text[])
        ORDER BY ps.updated_at DESC
        LIMIT 1
        """,
        list(SETTINGS_KEYS),
    )
    if not row:
        return {"updated_at": None, "updated_by_name": None}
    return {"updated_at": row["updated_at"], "updated_by_name": row["updated_by_name"]}


async def update_platform_settings(values: dict, *, user_id: int) -> PlatformSettings:
    async with pool.connection() as conn:
        async with conn.transaction():
            before = await _load(conn)
            for key in SETTINGS_KEYS:
                await conn.execute(
                    """
                    INSERT INTO platform_settings (key, value, updated_by, updated_at)
                    VALUES ($1, $2::jsonb, $3, NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    """,
                    key,
                    json.dumps(values.get(key)),
                    user_id,
                )
            after = await _load(conn)
            await AuditLogger(conn).log_action(
                user_id=user_id,
                actor_id=None,
                company_id=None,
                action="platform_settings.update",
                resource_type="platform_settings",
                resource_id="commission",
                details={"before": asdict(before), "after": asdict(after)},
            )
    invalidate_platform_settings_cache()
    return after
