import pytest

from app.core.exceptions import ValidationError
from app.modules.finance.services.commission import (
    calculate_commission,
    ensure_contract_amount_allowed,
    split_commission,
)
from app.modules.settings.service import PlatformSettings


def _settings(percent: float = 5, minimum: float = 0, limit: float | None = None) -> PlatformSettings:
    return PlatformSettings(
        commission_percent=percent,
        commission_min=minimum,
        max_contract_amount=limit,
    )


def test_commission_uses_percent_above_minimum():
    quote = calculate_commission(10_000, "TJS", _settings(percent=5, minimum=100))
    assert quote.amount == 500
    assert quote.percent == 5
    assert quote.minimum == 100


def test_commission_uses_minimum_for_small_contracts():
    assert calculate_commission(1_000, "TJS", _settings(percent=5, minimum=100)).amount == 100


def test_commission_never_exceeds_contract_amount():
    assert calculate_commission(60, "TJS", _settings(percent=5, minimum=100)).amount == 60


def test_commission_minimum_applies_only_to_tjs():
    quote = calculate_commission(1_000, "USD", _settings(percent=5, minimum=100))
    assert quote.amount == 50
    assert quote.minimum == 0


def test_commission_zero_amount():
    assert calculate_commission(0, "TJS", _settings(minimum=100)).amount == 0


def test_split_commission_is_proportional_and_exact():
    assert split_commission(100, [30, 40, 30]) == [30, 40, 30]
    parts = split_commission(100, [33.33, 33.33, 33.34])
    assert round(sum(parts), 2) == 100
    assert parts[-1] == pytest.approx(33.34, abs=0.01)


def test_split_commission_edge_cases():
    assert split_commission(100, []) == []
    assert split_commission(0, [50, 50]) == [0.0, 0.0]


def test_contract_limit_blocks_only_tjs_above_cap():
    settings = _settings(limit=10_000)
    ensure_contract_amount_allowed(10_000, "TJS", settings)
    ensure_contract_amount_allowed(50_000, "USD", settings)
    ensure_contract_amount_allowed(50_000, "TJS", _settings(limit=None))
    with pytest.raises(ValidationError) as exc:
        ensure_contract_amount_allowed(10_000.01, "TJS", settings)
    assert "не может превышать" in exc.value.detail
