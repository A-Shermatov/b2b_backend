import pytest

from app.modules.trust.levels import TIER_LABELS_RU, resolve_reputation_tier


def _tier(completed, rating, success_rate, status="verified"):
    return resolve_reputation_tier(
        verification_status=status,
        completed_contracts=completed,
        rating=rating,
        success_rate=success_rate,
    )


def test_unverified_is_none():
    assert _tier(500, 5.0, 100.0, status="pending") == "none"


def test_verified_without_deals_has_no_mark():
    assert _tier(0, 0, None) == "none"


@pytest.mark.parametrize(
    ("completed", "rating", "success_rate", "expected"),
    [
        (9, 5.0, 100.0, "none"),
        (10, 4.5, 90.0, "silver"),
        (39, 5.0, 100.0, "silver"),
        (40, 4.7, 95.0, "gold"),
        (99, 5.0, 100.0, "gold"),
        (100, 4.8, 98.0, "diamond"),
        # One weak metric holds the company on the lower step.
        (100, 4.79, 99.0, "gold"),
        (100, 5.0, 97.9, "gold"),
        (40, 4.6, 100.0, "silver"),
        (10, 4.4, 100.0, "none"),
    ],
)
def test_ladder_matches_frontend_thresholds(completed, rating, success_rate, expected):
    assert _tier(completed, rating, success_rate) == expected


def test_missing_success_rate_gives_no_mark():
    assert _tier(150, 5.0, None) == "none"


def test_every_tier_has_a_label():
    assert set(TIER_LABELS_RU) == {"none", "silver", "gold", "diamond"}
