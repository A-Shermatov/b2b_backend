from datetime import datetime, timezone

from app.modules.subscription.schemas import SubscriptionResponse

COMPANY_LIMITS: dict[str, int | None] = {
    "none": 1,
    "start": 2,
    "pro": 5,
    "business": None,
}

CATALOG_LIMITS: dict[str, int | None] = {
    "none": 10,
    "start": 10,
    "pro": 50,
    "business": None,
}


PAID_PLANS = ("start", "pro", "business")

# A plan is bought for one cabinet. Someone who both buys and sells pays twice.
SIDES = ("supplier", "buyer")
DEFAULT_SIDE = "supplier"


def clean_side(side: str | None) -> str:
    """'buyer' or 'supplier'; the value goes into SQL text, so nothing else passes."""
    if side not in SIDES:
        raise ValueError(f"Unknown subscription side: {side!r}")
    return side


def active_plan_sql(owner_column: str, side: str = DEFAULT_SIDE) -> str:
    """Active paid plan of the user in `owner_column` for one cabinet, or NULL."""
    plans = ", ".join(f"'{plan}'" for plan in PAID_PLANS)
    return f"""(
        SELECT ss.plan::text FROM supplier_subscriptions ss
        WHERE ss.user_id = {owner_column}
          AND ss.side = '{clean_side(side)}'
          AND ss.plan::text IN ({plans})
          AND ss.active_until IS NOT NULL
          AND ss.active_until > NOW()
    )"""


def plan_rank_sql(owner_column: str, side: str = DEFAULT_SIDE) -> str:
    """0 without a paid plan, then 1, 2, 3 as the plan grows.

    Lists sort by it first: what a subscriber posts stands above the rest, and a
    higher plan above a lower one.
    """
    return f"""COALESCE((
        SELECT CASE ss.plan::text WHEN 'business' THEN 3 WHEN 'pro' THEN 2 WHEN 'start' THEN 1 ELSE 0 END
        FROM supplier_subscriptions ss
        WHERE ss.user_id = {owner_column}
          AND ss.side = '{clean_side(side)}'
          AND ss.active_until IS NOT NULL
          AND ss.active_until > NOW()
    ), 0)"""


def actor_owner_sql(actor_id_column: str) -> str:
    """The user who pays for the actor in `actor_id_column`: the company owner, or the person."""
    return f"""(
        SELECT COALESCE(poc.owner_id, poa.user_id)
        FROM actors poa
        LEFT JOIN companies poc ON poc.id = poa.company_id
        WHERE poa.id = {actor_id_column}
    )"""


def is_active_plan(plan: str, active_until) -> bool:
    if plan == "none" or active_until is None:
        return False
    now = datetime.now(timezone.utc)
    if active_until.tzinfo is None:
        active_until = active_until.replace(tzinfo=timezone.utc)
    return active_until > now


def shape_subscription(plan: str, active_until) -> SubscriptionResponse:
    return SubscriptionResponse(
        plan=plan,
        active_until=active_until,
        is_active=is_active_plan(plan, active_until),
    )
