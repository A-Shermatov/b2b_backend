from app.modules.trust.levels import resolve_reputation_tier


def test_unverified_is_none():
    assert (
        resolve_reputation_tier(
            verification_status="pending",
            completed_contracts=100,
            rating=5.0,
            success_rate=100.0,
        )
        == "none"
    )


def test_verified_floor():
    assert (
        resolve_reputation_tier(
            verification_status="verified",
            completed_contracts=0,
            rating=0,
            success_rate=None,
        )
        == "verified"
    )


def test_shield_and_higher():
    assert (
        resolve_reputation_tier(
            verification_status="verified",
            completed_contracts=5,
            rating=4.0,
            success_rate=80.0,
        )
        == "shield"
    )
    assert (
        resolve_reputation_tier(
            verification_status="verified",
            completed_contracts=20,
            rating=4.5,
            success_rate=90.0,
        )
        == "silver"
    )
    assert (
        resolve_reputation_tier(
            verification_status="verified",
            completed_contracts=50,
            rating=4.7,
            success_rate=95.0,
        )
        == "gold"
    )


def test_missing_success_rate_blocks_silver_gold():
    assert (
        resolve_reputation_tier(
            verification_status="verified",
            completed_contracts=50,
            rating=5.0,
            success_rate=None,
        )
        == "shield"
    )
