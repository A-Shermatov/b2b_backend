"""QA: escrow state in our DB must stay in sync with the Alif Marketplace hold.

Each test describes the expected (correct) behaviour. A failing test means the
platform shows one thing to the buyer/supplier while the bank does another.
Alif is replaced by an in-memory fake; no network calls are made.
"""

import json
import secrets
from decimal import Decimal

import pytest

from app.config import Settings
from app.core.exceptions import ConflictError
from app.modules.finance.services.commission import calculate_commission, split_commission
from app.modules.payments.gateways.alif import AlifGatewayError
from app.modules.payments.services import PaymentService
from app.modules.settings.service import PlatformSettings
from tests.admin.test_disputes import _create_contract, _open_dispute
from tests.sql import execute, fetch, fetchval, insert_work_submission

API = "/api/v1"
SUPPLIER_TERMINAL = "610184"
PLATFORM_TERMINAL = "841418"


def _marketplace_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://unused",
        payment_provider="alif",
        alif_terminal_key="qa-key",
        alif_terminal_password="qa-password",
        alif_test_merchant_1=SUPPLIER_TERMINAL,
        alif_test_merchant_2=PLATFORM_TERMINAL,
    )


class _FakeAlif:
    confirmed: list[str] = []
    canceled: list[str] = []
    amounts: dict[str, Decimal] = {}
    fail_cancel: set[str] = set()
    on_confirm = None

    def __init__(self, settings=None):
        pass

    async def confirm_delivery(self, *, transaction_id, amount, order_id=None):
        _FakeAlif.confirmed.append(transaction_id)
        _FakeAlif.amounts[transaction_id] = amount
        if _FakeAlif.on_confirm:
            await _FakeAlif.on_confirm()
        return {}

    async def cancel_marketplace(self, *, transaction_id, amount, reason="", order_id=None):
        if transaction_id in _FakeAlif.fail_cancel:
            raise AlifGatewayError("boom")
        _FakeAlif.canceled.append(transaction_id)
        _FakeAlif.amounts[transaction_id] = amount
        return {}

    async def check_transaction(self, order_id):
        return None


@pytest.fixture
def fake_alif(monkeypatch):
    _FakeAlif.confirmed = []
    _FakeAlif.canceled = []
    _FakeAlif.amounts = {}
    _FakeAlif.fail_cancel = set()
    _FakeAlif.on_confirm = None
    settings = _marketplace_settings()
    monkeypatch.setattr("app.modules.payments.services.gateway.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.payments.services.gateway.AlifClient", _FakeAlif)
    return _FakeAlif


async def _milestones(contract_id: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT id, amount, status::text AS status FROM payment_milestones
        WHERE contract_id = $1 ORDER BY id
        """,
        contract_id,
    )
    return [dict(r) for r in rows]


async def _two_funded_milestones(contract_id: int) -> list[dict]:
    """10000 + 5000 TJS, both funded (held by the bank)."""
    await execute(
        "UPDATE payment_milestones SET amount = 10000, status = 'funded' WHERE contract_id = $1",
        contract_id,
    )
    await execute(
        """
        INSERT INTO payment_milestones (
            payment_plan_id, contract_id, title, percentage, amount, trigger, status,
            commission_amount
        )
        SELECT payment_plan_id, contract_id, 'Этап 2', 33, 5000, trigger, 'funded', 0
        FROM payment_milestones WHERE contract_id = $1 ORDER BY id LIMIT 1
        """,
        contract_id,
    )
    await execute("UPDATE contracts SET status = 'active' WHERE id = $1", contract_id)
    return await _milestones(contract_id)


async def _held_tx(milestone_id: int, contract_id: int, amount, *, status="to_approve") -> str:
    """Insert an Alif marketplace order for one milestone with its own sub-transactions."""
    order_id = f"MS{milestone_id}-{secrets.token_hex(6)}"
    buyer_actor_id = await fetchval(
        "SELECT buyer_actor_id FROM contracts WHERE id = $1", contract_id
    )
    meta = {
        "mpTerminalInfo": [
            {"terminal_id": SUPPLIER_TERMINAL, "amount": float(amount)},
        ],
        "sub_transactions": [
            {"transaction_id": f"sub-{order_id}", "terminal_id": SUPPLIER_TERMINAL},
        ],
    }
    await execute(
        """
        INSERT INTO payment_gateway_transactions
            (order_id, gateway, milestone_id, contract_id, buyer_actor_id, amount, currency,
             status, marketplace_meta)
        VALUES ($1, 'alif', $2, $3, $4, $5, 'TJS', $6, $7::jsonb)
        """,
        order_id,
        milestone_id,
        contract_id,
        buyer_actor_id,
        Decimal(str(amount)),
        status,
        json.dumps(meta),
    )
    return order_id


async def _tx_status(order_id: str) -> str:
    return await fetchval(
        "SELECT status FROM payment_gateway_transactions WHERE order_id = $1", order_id
    )


async def _dispute_id(contract_id: int) -> int:
    return await fetchval(
        "SELECT id FROM disputes WHERE contract_id = $1 ORDER BY id DESC LIMIT 1", contract_id
    )


@pytest.mark.asyncio
async def test_partial_refund_moves_money_in_bank(
    client, admin_auth, buyer_auth, supplier_auth, fake_alif
):
    """Dispute partial refund: released part must be confirmed, refunded part canceled in Alif."""
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA partial refund")
    m1, m2 = await _two_funded_milestones(contract_id)
    tx1 = await _held_tx(m1["id"], contract_id, m1["amount"])
    tx2 = await _held_tx(m2["id"], contract_id, m2["amount"])
    await _open_dispute(client, buyer_auth, contract_id, "Часть работы не выполнена")

    res = await client.post(
        f"{API}/admin/disputes/{await _dispute_id(contract_id)}/action",
        headers=admin_auth["headers"],
        json={"action": "partial_refund", "reason": "QA", "partial_buyer_amount": 10000},
    )
    assert res.status_code == 200, res.text
    statuses = {m["id"]: m["status"] for m in await _milestones(contract_id)}
    assert statuses == {m1["id"]: "refunded", m2["id"]: "released"}

    # In our DB the buyer got 10000 back and the supplier got 5000 —
    # the bank must reflect the same, nothing may stay on hold.
    assert await _tx_status(tx1) == "canceled", "refunded milestone still held in bank"
    assert await _tx_status(tx2) == "ok", "released milestone never confirmed in bank"


@pytest.mark.asyncio
async def test_force_complete_after_freeze_releases_what_bank_paid(
    client, admin_auth, buyer_auth, supplier_auth, fake_alif
):
    """Freeze -> force_complete: bank pays supplier, so milestones must become released."""
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA freeze complete")
    m1, m2 = await _two_funded_milestones(contract_id)
    await _held_tx(m1["id"], contract_id, m1["amount"])
    await _held_tx(m2["id"], contract_id, m2["amount"])

    for action in ("freeze", "force_complete"):
        res = await client.post(
            f"{API}/admin/contracts/{contract_id}/action",
            headers=admin_auth["headers"],
            json={"action": action, "reason": "QA"},
        )
        assert res.status_code == 200, res.text

    assert len(fake_alif.confirmed) == 2, "bank confirm-delivery was called for both"
    statuses = [m["status"] for m in await _milestones(contract_id)]
    assert statuses == ["released", "released"], (
        f"bank paid the supplier but milestones are {statuses}; no invoice / balance recorded"
    )


@pytest.mark.asyncio
async def test_cancel_after_partial_release_keeps_released_money(
    client, admin_auth, buyer_auth, supplier_auth, fake_alif
):
    """Admin cancel must refund only what is still in escrow, not already released milestones."""
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA cancel released")
    m1, m2 = await _two_funded_milestones(contract_id)
    released_tx = await _held_tx(m1["id"], contract_id, m1["amount"], status="ok")
    await execute("UPDATE payment_milestones SET status = 'released' WHERE id = $1", m1["id"])
    held_tx = await _held_tx(m2["id"], contract_id, m2["amount"])

    res = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        headers=admin_auth["headers"],
        json={"action": "cancel", "reason": "QA"},
    )
    assert res.status_code == 200, res.text

    assert f"sub-{held_tx}" in fake_alif.canceled
    assert f"sub-{released_tx}" not in fake_alif.canceled, (
        "cancel reversed a milestone that was already released to the supplier"
    )
    statuses = [m["status"] for m in await _milestones(contract_id)]
    assert statuses == ["released", "refunded"]


@pytest.mark.asyncio
async def test_approve_does_not_pay_supplier_if_dispute_opened_meanwhile(
    client, buyer_auth, supplier_auth, fake_alif
):
    """approve_milestone confirms delivery in the bank before re-checking disputes."""
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA approve race")
    m1, _ = await _two_funded_milestones(contract_id)
    await _held_tx(m1["id"], contract_id, m1["amount"])

    async def open_dispute_concurrently():
        await execute(
            """
            INSERT INTO disputes (contract_id, status, created_at, updated_at)
            VALUES ($1, 'open', NOW(), NOW())
            """,
            contract_id,
        )

    fake_alif.on_confirm = open_dispute_concurrently
    res = await client.post(
        f"{API}/buyer/payments/milestones/{m1['id']}/approve", headers=buyer_auth["headers"]
    )
    status = await fetchval("SELECT status::text FROM payment_milestones WHERE id = $1", m1["id"])
    if fake_alif.confirmed:
        assert status == "released", (
            f"bank released money to supplier, API answered {res.status_code}, "
            f"milestone left '{status}' and frozen by dispute"
        )


@pytest.mark.asyncio
async def test_bank_cancel_callback_blocks_release(client, buyer_auth, supplier_auth, fake_alif):
    """After Alif reports the hold canceled, buyer must not be able to 'release' it."""
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA bank cancel")
    m1, _ = await _two_funded_milestones(contract_id)
    await execute("UPDATE payment_milestones SET status = 'awaiting_payment' WHERE id = $1", m1["id"])
    order_id = await _held_tx(m1["id"], contract_id, m1["amount"], status="pending")
    service = PaymentService()

    await service.apply_gateway_status(order_id, "to_approve", "T-1", {"status": "to_approve"})
    assert await fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", m1["id"]
    ) == "funded"

    await service.apply_gateway_status(order_id, "canceled", "T-1", {"status": "canceled"})
    assert await _tx_status(order_id) == "canceled"

    milestone_status = await fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", m1["id"]
    )
    buyer_actor_id = await fetchval(
        "SELECT buyer_actor_id FROM contracts WHERE id = $1", contract_id
    )
    released = True
    try:
        await service.approve_milestone(m1["id"], buyer_actor_id)
    except ConflictError:
        released = False
    assert not released, (
        f"bank canceled the payment, milestone stayed '{milestone_status}' and was released "
        "to the supplier balance"
    )


@pytest.mark.asyncio
async def test_alif_release_is_recorded_with_alif_gateway(
    client, buyer_auth, supplier_auth, fake_alif
):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA ledger gateway")
    m1, _ = await _two_funded_milestones(contract_id)
    await _held_tx(m1["id"], contract_id, m1["amount"])
    buyer_actor_id = await fetchval(
        "SELECT buyer_actor_id FROM contracts WHERE id = $1", contract_id
    )
    await PaymentService().approve_milestone(m1["id"], buyer_actor_id)
    gateways = await fetch(
        "SELECT DISTINCT gateway::text AS g FROM platform_payments WHERE contract_id = $1",
        contract_id,
    )
    assert [r["g"] for r in gateways] == ["alif"], (
        f"real Alif release recorded in ledger as {[r['g'] for r in gateways]}"
    )


def test_marketplace_parts_meet_alif_minimum():
    """Alif rejects any mpTerminalInfo part below 1 TJS ("Сумма должна быть от 1 смн").

    Seen on test-web.alif.tj: a 10 TJS deal with 5% commission (0.5 TJS) cannot be paid.
    """
    from app.modules.payments.gateways.alif import build_mp_terminal_info

    entries = build_mp_terminal_info(
        total=Decimal("10"),
        commission=Decimal("0.5"),
        supplier_terminal_id=SUPPLIER_TERMINAL,
        platform_terminal_id=PLATFORM_TERMINAL,
        title="QA",
    )
    amounts = [e["amount"] for e in entries]
    assert sum(amounts) == 10
    assert all(a >= 1 for a in amounts), f"parts {amounts} will be rejected by Alif"


async def _tx_meta(order_id: str) -> dict:
    meta = await fetchval(
        "SELECT marketplace_meta FROM payment_gateway_transactions WHERE order_id = $1", order_id
    )
    return json.loads(meta) if isinstance(meta, str) else (meta or {})


async def _open_dispute_row(contract_id: int) -> None:
    await execute(
        """
        INSERT INTO disputes (contract_id, status, created_at, updated_at)
        VALUES ($1, 'open', NOW(), NOW())
        """,
        contract_id,
    )
    await execute("UPDATE contracts SET status = 'disputed' WHERE id = $1", contract_id)


@pytest.mark.asyncio
async def test_dispute_blocks_release_when_bank_did_not_pay(
    client, buyer_auth, supplier_auth, monkeypatch
):
    """Without a marketplace payout a dispute opened meanwhile still blocks the release."""
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA mock race")
    m1, _ = await _two_funded_milestones(contract_id)

    async def no_payout(self, milestone_id):
        await _open_dispute_row(contract_id)
        return False

    monkeypatch.setattr(PaymentService, "confirm_marketplace_delivery_for_milestone", no_payout)
    res = await client.post(
        f"{API}/buyer/payments/milestones/{m1['id']}/approve", headers=buyer_auth["headers"]
    )
    assert res.status_code == 409, res.text
    assert await fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", m1["id"]
    ) == "funded"


@pytest.mark.asyncio
async def test_submission_paid_out_during_dispute_keeps_contract_disputed(
    client, buyer_auth, supplier_auth, fake_alif
):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA submit race")
    m1, m2 = await _two_funded_milestones(contract_id)
    await _held_tx(m1["id"], contract_id, m1["amount"])
    await _held_tx(m2["id"], contract_id, m2["amount"])
    await execute("UPDATE contracts SET status = 'delivered' WHERE id = $1", contract_id)
    submission_id = await insert_work_submission(contract_id=contract_id, note="QA")

    async def open_dispute_once():
        fake_alif.on_confirm = None
        await _open_dispute_row(contract_id)

    fake_alif.on_confirm = open_dispute_once
    res = await client.post(
        f"{API}/buyer/contracts/{contract_id}/submissions/{submission_id}/approve",
        headers=buyer_auth["headers"],
    )
    assert res.status_code == 200, res.text
    assert [m["status"] for m in await _milestones(contract_id)] == ["released", "released"]
    assert await fetchval(
        "SELECT status::text FROM contracts WHERE id = $1", contract_id
    ) == "disputed"


@pytest.mark.asyncio
async def test_failed_cancel_does_not_hide_a_later_bank_cancel(
    client, admin_auth, buyer_auth, supplier_auth, fake_alif
):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA failed cancel")
    m1, _ = await _two_funded_milestones(contract_id)
    order_id = await _held_tx(m1["id"], contract_id, m1["amount"])
    await execute(
        "UPDATE payment_milestones SET status = 'released' WHERE contract_id = $1 AND id <> $2",
        contract_id,
        m1["id"],
    )
    fake_alif.fail_cancel = {f"sub-{order_id}"}

    res = await client.post(
        f"{API}/admin/contracts/{contract_id}/action",
        headers=admin_auth["headers"],
        json={"action": "cancel", "reason": "QA"},
    )
    assert res.status_code == 409, res.text
    assert "cancel_requested" not in await _tx_meta(order_id)

    await PaymentService().apply_gateway_status(order_id, "canceled", "T-1", {"status": "canceled"})
    assert await fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", m1["id"]
    ) == "awaiting_payment"


@pytest.mark.asyncio
async def test_payment_after_cancellation_is_refunded(
    client, buyer_auth, supplier_auth, fake_alif
):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA late payment")
    m1, _ = await _two_funded_milestones(contract_id)
    await execute("UPDATE payment_milestones SET status = 'awaiting_payment' WHERE id = $1", m1["id"])
    order_id = await _held_tx(m1["id"], contract_id, m1["amount"], status="pending")
    await execute(
        "UPDATE payment_gateway_transactions SET marketplace_meta = '{}'::jsonb WHERE order_id = $1",
        order_id,
    )
    service = PaymentService()

    await service.cancel_marketplace_for_contract(contract_id)
    assert await _tx_status(order_id) == "canceled"

    late_subs = [{"transaction_id": "late-1", "terminal_id": SUPPLIER_TERMINAL}]
    await service.apply_gateway_status(
        order_id, "to_approve", "T-9", {"status": "to_approve", "sub_transactions": late_subs}
    )
    assert fake_alif.canceled == ["late-1"]
    assert await _tx_status(order_id) == "canceled"
    assert await fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", m1["id"]
    ) == "awaiting_payment"


@pytest.mark.asyncio
async def test_bank_cancel_reverts_contract_activation(
    client, buyer_auth, supplier_auth, fake_alif
):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA deactivate")
    milestone_id = await fetchval(
        "SELECT id FROM payment_milestones WHERE contract_id = $1", contract_id
    )
    await execute(
        "UPDATE payment_milestones SET status = 'awaiting_payment' WHERE id = $1", milestone_id
    )
    await execute("UPDATE contracts SET status = 'pending_payment' WHERE id = $1", contract_id)
    amount = await fetchval("SELECT amount FROM payment_milestones WHERE id = $1", milestone_id)
    order_id = await _held_tx(milestone_id, contract_id, amount, status="pending")
    service = PaymentService()

    await service.apply_gateway_status(order_id, "to_approve", "T-1", {"status": "to_approve"})
    assert await fetchval("SELECT status::text FROM contracts WHERE id = $1", contract_id) == "active"
    await service.apply_gateway_status(order_id, "canceled", "T-1", {"status": "canceled"})
    assert await fetchval(
        "SELECT status::text FROM contracts WHERE id = $1", contract_id
    ) == "pending_payment"


@pytest.mark.asyncio
async def test_legacy_order_without_split_confirms_rebuilt_amounts(
    client, buyer_auth, supplier_auth, fake_alif
):
    contract_id = await _create_contract(client, buyer_auth, supplier_auth, "QA legacy split")
    m1, _ = await _two_funded_milestones(contract_id)
    await execute("UPDATE payment_milestones SET commission_amount = 500 WHERE id = $1", m1["id"])
    order_id = await _held_tx(m1["id"], contract_id, m1["amount"])
    legacy = {
        "sub_transactions": [
            {"transaction_id": "leg-s", "terminal_id": SUPPLIER_TERMINAL},
            {"transaction_id": "leg-p", "terminal_id": PLATFORM_TERMINAL},
        ]
    }
    await execute(
        "UPDATE payment_gateway_transactions SET marketplace_meta = $2::jsonb WHERE order_id = $1",
        order_id,
        json.dumps(legacy),
    )

    await PaymentService().confirm_marketplace_delivery_for_milestone(m1["id"])
    assert fake_alif.amounts == {"leg-s": Decimal("9500.00"), "leg-p": Decimal("500.00")}


def test_bank_minimum_applies_only_to_bank_split():
    settings = PlatformSettings(commission_percent=5, commission_min=0)
    assert calculate_commission(10, "TJS", settings).amount == 0.5
    assert calculate_commission(10, "TJS", settings, bank_split=True).amount == 1.0


@pytest.mark.parametrize(
    ("total", "amounts", "expected"),
    [
        (1.5, [5, 5, 5], [0.0, 1.5, 0.0]),
        (2.4, [16, 8], [2.4, 0.0]),
        (3.0, [50, 50], [1.5, 1.5]),
    ],
)
def test_split_commission_keeps_parts_payable(total, amounts, expected):
    parts = split_commission(total, amounts, bank_split=True)
    assert parts == expected
    assert round(sum(parts), 2) == total
    assert all(part == 0 or part >= 1 for part in parts)
