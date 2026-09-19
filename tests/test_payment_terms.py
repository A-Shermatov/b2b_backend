from types import SimpleNamespace

import pytest

from app.enums import PaymentMilestoneStatus, PaymentMilestoneTrigger, PaymentType
from app.modules.contracts.services import _custom_milestones, _default_milestones
from app.modules.proposals.schemas import ProposalAcceptRequest


def _contract(amount: float):
    return SimpleNamespace(agreed_amount=amount)


def test_default_split_payment_milestones():
    milestones = _default_milestones(_contract(1000), PaymentType.split_payment)
    assert len(milestones) == 2
    assert [m["percentage"] for m in milestones] == [50, 50]
    assert sum(m["amount"] for m in milestones) == 1000
    assert milestones[0]["status"] == PaymentMilestoneStatus.awaiting_payment
    assert milestones[1]["status"] == PaymentMilestoneStatus.pending


def test_default_full_prepayment_milestones():
    milestones = _default_milestones(_contract(500), PaymentType.full_prepayment)
    assert len(milestones) == 1
    assert milestones[0]["amount"] == 500
    assert milestones[0]["status"] == PaymentMilestoneStatus.awaiting_payment


def test_custom_milestones_absorb_rounding():
    items = [
        {"title": "Этап 1", "percentage": 33.33, "trigger": PaymentMilestoneTrigger.contract_signed},
        {"title": "Этап 2", "percentage": 33.33, "trigger": PaymentMilestoneTrigger.delivery_accepted},
        {"title": "Этап 3", "percentage": 33.34, "trigger": PaymentMilestoneTrigger.delivery_accepted},
    ]
    milestones = _custom_milestones(_contract(1000), items)
    assert len(milestones) == 3
    assert sum(m["amount"] for m in milestones) == 1000
    assert milestones[0]["status"] == PaymentMilestoneStatus.awaiting_payment
    assert milestones[1]["status"] == PaymentMilestoneStatus.pending
    assert milestones[0]["trigger"] == PaymentMilestoneTrigger.contract_signed.value


def test_accept_request_requires_milestones_for_milestone_type():
    with pytest.raises(ValueError):
        ProposalAcceptRequest(payment_type=PaymentType.milestone, milestones=None)


def test_accept_request_milestones_must_sum_to_100():
    with pytest.raises(ValueError):
        ProposalAcceptRequest(
            payment_type=PaymentType.milestone,
            milestones=[
                {"title": "A", "percentage": 40, "trigger": PaymentMilestoneTrigger.contract_signed},
                {"title": "B", "percentage": 40, "trigger": PaymentMilestoneTrigger.delivery_accepted},
            ],
        )


def test_accept_request_clears_milestones_for_non_milestone_type():
    req = ProposalAcceptRequest(
        payment_type=PaymentType.split_payment,
        milestones=[
            {"title": "A", "percentage": 50, "trigger": PaymentMilestoneTrigger.contract_signed},
            {"title": "B", "percentage": 50, "trigger": PaymentMilestoneTrigger.delivery_accepted},
        ],
    )
    assert req.milestones is None
