"""Free reliability marks (знак надёжности) earned by platform activity — not paid plans.

Separate from actors.trust_level, which still mirrors document verification.
The ladder and thresholds must match the frontend (lib/trust-levels.ts).
"""

from __future__ import annotations

from typing import Any

# Highest first — first match wins.
TIER_ORDER = ("diamond", "gold", "silver", "none")

TIER_LABELS_RU = {
    "none": "Без знака",
    "silver": "Серебро",
    "gold": "Золото",
    "diamond": "Алмаз",
}

# Platform thresholds (adjustable without schema changes).
THRESHOLDS: dict[str, dict[str, float | int]] = {
    "silver": {"min_completed": 10, "min_rating": 4.5, "min_success_rate": 90.0},
    "gold": {"min_completed": 40, "min_rating": 4.7, "min_success_rate": 95.0},
    "diamond": {"min_completed": 100, "min_rating": 4.8, "min_success_rate": 98.0},
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
    if success is None:
        return "none"

    for tier in TIER_ORDER[:-1]:
        limits = THRESHOLDS[tier]
        if (
            completed >= int(limits["min_completed"])
            and rating_v >= float(limits["min_rating"])
            and success >= float(limits["min_success_rate"])
        ):
            return tier
    return "none"


def reputation_tier_label(tier: str | None) -> str:
    return TIER_LABELS_RU.get(tier or "none", TIER_LABELS_RU["none"])


def reputation_from_public_fields(data: dict[str, Any]) -> str:
    return resolve_reputation_tier(
        verification_status=data.get("verification_status"),
        completed_contracts=int(data.get("completed_contracts") or 0),
        rating=float(data.get("rating") or 0),
        success_rate=data.get("success_rate"),
    )
