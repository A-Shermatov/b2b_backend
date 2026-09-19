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
