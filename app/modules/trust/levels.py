"""Free reputation tiers (щиты) earned by platform activity — not paid plans.

Separate from actors.trust_level, which still mirrors document verification.
"""

from __future__ import annotations

from typing import Any

# Highest first — first match wins.
TIER_ORDER = ("gold", "silver", "shield", "verified", "none")

TIER_LABELS_RU = {
    "none": "Без уровня",
    "verified": "Проверенный",
    "shield": "Щит",
    "silver": "Серебряный щит",
    "gold": "Золотой щит",
}

# Platform thresholds (adjustable without schema changes).
THRESHOLDS: dict[str, dict[str, float | int]] = {
    "verified": {},  # verification only
    "shield": {"min_completed": 5, "min_rating": 4.0},
    "silver": {"min_completed": 20, "min_rating": 4.5, "min_success_rate": 90.0},
    "gold": {"min_completed": 50, "min_rating": 4.7, "min_success_rate": 95.0},
}


def resolve_reputation_tier(
    *,
    verification_status: str | None,
    completed_contracts: int = 0,
    rating: float = 0.0,
    success_rate: float | None = None,
) -> str:
    verified = (verification_status or "").lower() == "verified"
    if not verified:
        return "none"

    completed = max(0, int(completed_contracts or 0))
    rating_v = float(rating or 0)
    success = float(success_rate) if success_rate is not None else None

    gold = THRESHOLDS["gold"]
    if (
        completed >= int(gold["min_completed"])
        and rating_v >= float(gold["min_rating"])
        and success is not None
        and success >= float(gold["min_success_rate"])
    ):
        return "gold"

    silver = THRESHOLDS["silver"]
    if (
        completed >= int(silver["min_completed"])
        and rating_v >= float(silver["min_rating"])
        and success is not None
        and success >= float(silver["min_success_rate"])
    ):
        return "silver"

    shield = THRESHOLDS["shield"]
    if completed >= int(shield["min_completed"]) and rating_v >= float(shield["min_rating"]):
        return "shield"

    return "verified"


def reputation_tier_label(tier: str | None) -> str:
    return TIER_LABELS_RU.get(tier or "none", TIER_LABELS_RU["none"])


def reputation_from_public_fields(data: dict[str, Any]) -> str:
    return resolve_reputation_tier(
        verification_status=data.get("verification_status"),
        completed_contracts=int(data.get("completed_contracts") or 0),
        rating=float(data.get("rating") or 0),
        success_rate=data.get("success_rate"),
    )
