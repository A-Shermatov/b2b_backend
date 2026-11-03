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


def active_plan_sql(owner_column: str) -> str:
    """Active paid plan of the user in `owner_column`, or NULL (one row per user)."""
    plans = ", ".join(f"'{plan}'" for plan in PAID_PLANS)
    return f"""(
        SELECT ss.plan::text FROM supplier_subscriptions ss
        WHERE ss.user_id = {owner_column}
          AND ss.plan::text IN ({plans})
          AND ss.active_until IS NOT NULL
          AND ss.active_until > NOW()
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
