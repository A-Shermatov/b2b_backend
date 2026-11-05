import json
import secrets
from decimal import Decimal

import pytest

from app.config import Settings
from app.core.exceptions import ConflictError
from app.modules.finance.schemas import WithdrawalCreate
from app.modules.finance.services import SupplierFinanceService
from app.modules.payments.gateways.alif import AlifGatewayError
from app.modules.payments.services import PaymentService
from app.modules.subscription.services import SubscriptionService
from test_alif_gateway import _create_awaiting_milestone, _settings, db_conn  # noqa: F401

MARKETPLACE_META = {
    "mpTerminalInfo": [
        {"terminal_id": "610184", "amount": 97},
        {"terminal_id": "841418", "amount": 3},
    ],
    "sub_transactions": [
        {"transaction_id": "sub-a", "terminal_id": "610184"},
        {"transaction_id": "sub-b", "terminal_id": "841418"},
    ],
}


TEST_SECRET = "audit-test-secret-key-0123456789abcdef"


def _mock_settings(**overrides) -> Settings:
    # Non-dev environments require a strong SECRET_KEY.
    values = {"payment_provider": "mock", "secret_key": TEST_SECRET, **overrides}
    return _settings(alif_terminal_key=None, alif_terminal_password=None, **values)


def _marketplace_settings() -> Settings:
    return _settings(alif_test_merchant_1="610184", alif_test_merchant_2="841418")


def test_allow_mock_payments_defaults_by_environment(monkeypatch):
    monkeypatch.delenv("ALLOW_MOCK_PAYMENTS", raising=False)
    assert _mock_settings(app_env="development").allow_mock_payments is True
    assert _mock_settings(app_env="production").allow_mock_payments is False
    assert _mock_settings(app_env="staging").allow_mock_payments is False


def test_allow_mock_payments_is_overridable(monkeypatch):
    assert _mock_settings(app_env="production", allow_mock_payments=True).allow_mock_payments
    assert not _mock_settings(app_env="development", allow_mock_payments=False).allow_mock_payments
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    assert Settings(
        _env_file=None,
        database_url="postgresql://unused",
        app_env="production",
        secret_key=TEST_SECRET,
    ).allow_mock_payments


async def test_subscription_checkout_fails_closed_without_gateway(monkeypatch):
    settings = _mock_settings(allow_mock_payments=False)
    monkeypatch.setattr("app.modules.subscription.services.payment.get_settings", lambda: settings)
    with pytest.raises(ConflictError, match="Платёжный шлюз не настроен"):
        await SubscriptionService().checkout(1, "start")
    with pytest.raises(ConflictError):
        await SubscriptionService().activate(1, "start")


async def test_subscription_checkout_rejects_alif_without_credentials(monkeypatch):
    settings = _settings(alif_terminal_key=None, alif_terminal_password=None)
    monkeypatch.setattr("app.modules.subscription.services.payment.get_settings", lambda: settings)
    with pytest.raises(AlifGatewayError):
        await SubscriptionService().checkout(1, "start")


async def test_mock_fund_and_confirm_fail_closed(monkeypatch):
    settings = _mock_settings(allow_mock_payments=False)
    monkeypatch.setattr("app.modules.payments.services.update.get_settings", lambda: settings)
    with pytest.raises(ConflictError, match="Платёжный шлюз не настроен"):
        await PaymentService().fund_milestone(1, 1)
    with pytest.raises(ConflictError, match="Платёжный шлюз не настроен"):
        await PaymentService().mock_confirm_payment(1, 1)


async def test_mock_fund_allowed_when_enabled(db_conn, monkeypatch):  # noqa: F811
    settings = _mock_settings(allow_mock_payments=True)
    monkeypatch.setattr("app.modules.payments.services.update.get_settings", lambda: settings)
    milestone = await _create_awaiting_milestone(db_conn)
    result = await PaymentService(db_conn).mock_confirm_payment(
        milestone["id"], milestone["buyer_actor_id"]
    )
    assert result["milestone_status"] == "funded"


async def test_accepted_unpaid_work_can_still_be_paid_and_released(db_conn, monkeypatch):  # noqa: F811
    settings = _mock_settings(allow_mock_payments=True)
    monkeypatch.setattr("app.modules.payments.services.update.get_settings", lambda: settings)
    milestone = await _create_awaiting_milestone(db_conn)
    await db_conn.execute(
        "UPDATE contracts SET status = 'completed' WHERE id = $1", milestone["contract_id"]
    )
    service = PaymentService(db_conn)
    funded = await service.fund_milestone(milestone["id"], milestone["buyer_actor_id"])
    assert funded["status"] == "funded"
    released = await service.approve_milestone(milestone["id"], milestone["buyer_actor_id"])
    assert released["status"] == "released"


@pytest.mark.parametrize("contract_status", ["cancelled", "disputed"])
async def test_fund_rejected_for_closed_contract(db_conn, monkeypatch, contract_status):  # noqa: F811
    settings = _mock_settings(allow_mock_payments=True)
    monkeypatch.setattr("app.modules.payments.services.update.get_settings", lambda: settings)
    milestone = await _create_awaiting_milestone(db_conn)
    await db_conn.execute(
        "UPDATE contracts SET status = $2::contract_status WHERE id = $1",
        milestone["contract_id"],
        contract_status,
    )
    with pytest.raises(ConflictError):
        await PaymentService(db_conn).fund_milestone(milestone["id"], milestone["buyer_actor_id"])
    status = await db_conn.fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", milestone["id"]
    )
    assert status == "awaiting_payment"


async def test_start_alif_payment_rejected_for_cancelled_contract(db_conn, monkeypatch):  # noqa: F811
    milestone = await _create_awaiting_milestone(db_conn)
    await db_conn.execute(
        "UPDATE contracts SET status = 'cancelled' WHERE id = $1", milestone["contract_id"]
    )
    with pytest.raises(ConflictError):
        await PaymentService(db_conn).start_alif_payment(
            milestone["id"], milestone["buyer_actor_id"]
        )


async def test_approve_requires_funded_milestone(db_conn):  # noqa: F811
    milestone = await _create_awaiting_milestone(db_conn)
    with pytest.raises(ConflictError):
        await PaymentService(db_conn).approve_milestone(
            milestone["id"], milestone["buyer_actor_id"]
        )


@pytest.mark.parametrize("dispute_status", ["open", "under_review", "appealed"])
async def test_approve_rejected_with_active_dispute(db_conn, dispute_status):  # noqa: F811
    milestone = await _create_awaiting_milestone(db_conn)
    await db_conn.execute(
        "UPDATE payment_milestones SET status = 'funded' WHERE id = $1", milestone["id"]
    )
    await db_conn.execute(
        """
        INSERT INTO disputes (contract_id, status, created_at, updated_at)
        VALUES ($1, $2::dispute_status, NOW(), NOW())
        """,
        milestone["contract_id"],
        dispute_status,
    )
    with pytest.raises(ConflictError, match="dispute"):
        await PaymentService(db_conn).approve_milestone(
            milestone["id"], milestone["buyer_actor_id"]
        )
    status = await db_conn.fetchval(
        "SELECT status::text FROM payment_milestones WHERE id = $1", milestone["id"]
    )
    assert status == "funded"


async def test_approve_releases_funded_milestone_without_dispute(db_conn):  # noqa: F811
    milestone = await _create_awaiting_milestone(db_conn)
    await db_conn.execute(
        "UPDATE payment_milestones SET status = 'funded' WHERE id = $1", milestone["id"]
    )
    await db_conn.execute(
        """
        INSERT INTO disputes (contract_id, status, created_at, updated_at)
        VALUES ($1, 'resolved'::dispute_status, NOW(), NOW())
        """,
        milestone["contract_id"],
    )
    result = await PaymentService(db_conn).approve_milestone(
        milestone["id"], milestone["buyer_actor_id"]
    )
    assert result == {"id": milestone["id"], "status": "released"}


async def _insert_held_transaction(conn, milestone, *, status: str, meta=MARKETPLACE_META):
    order_id = f"MS{milestone['id']}-{secrets.token_hex(6)}"
    await conn.execute(
        """
        INSERT INTO payment_gateway_transactions
            (order_id, gateway, milestone_id, contract_id, buyer_actor_id, amount, currency,
             status, marketplace_meta)
        VALUES ($1, 'alif', $2, $3, $4, $5, $6, $7, $8::jsonb)
        """,
        order_id,
        milestone["id"],
        milestone["contract_id"],
        milestone["buyer_actor_id"],
        Decimal("100.00"),
        milestone["currency"],
        status,
        json.dumps(meta),
    )
    return order_id


async def _tx_status(conn, order_id: str) -> str:
    return await conn.fetchval(
        "SELECT status FROM payment_gateway_transactions WHERE order_id = $1", order_id
    )


class _FakeAlif:
    confirmed: list[str]
    canceled: list[str]
    fail_cancel: set[str]
    checktxn_by_order: dict[str, dict]

    def __init__(self, settings=None):
        pass

    async def confirm_delivery(self, *, transaction_id, amount, order_id=None):
        _FakeAlif.confirmed.append(transaction_id)
        return {}

    async def cancel_marketplace(self, *, transaction_id, amount, reason="", order_id=None):
        if transaction_id in _FakeAlif.fail_cancel:
            raise AlifGatewayError("boom")
        _FakeAlif.canceled.append(transaction_id)
        return {}

    async def check_transaction(self, order_id: str):
        return _FakeAlif.checktxn_by_order.get(order_id)


@pytest.fixture
def fake_alif(monkeypatch):
    _FakeAlif.confirmed = []
    _FakeAlif.canceled = []
    _FakeAlif.fail_cancel = set()
    _FakeAlif.checktxn_by_order = {}
    settings = _marketplace_settings()
    monkeypatch.setattr("app.modules.payments.services.gateway.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.payments.services.gateway.AlifClient", _FakeAlif)
    return _FakeAlif


async def _funded_milestone(conn):
    """Bank holds are released only for funded milestones (as after a real payment)."""
    milestone = await _create_awaiting_milestone(conn)
    await conn.execute(
        "UPDATE payment_milestones SET status = 'funded' WHERE id = $1", milestone["id"]
    )
    return milestone


async def test_confirm_delivery_is_per_milestone_and_idempotent(db_conn, fake_alif):  # noqa: F811
    first = await _funded_milestone(db_conn)
    second = await _funded_milestone(db_conn)
    held = await _insert_held_transaction(db_conn, first, status="to_approve")
    other = await _insert_held_transaction(db_conn, second, status="to_approve")
    done = await _insert_held_transaction(
        db_conn,
        first,
        status="ok",
        meta={"sub_transactions": [{"transaction_id": "sub-done", "terminal_id": "610184"}]},
    )
    service = PaymentService(db_conn)

    await service.confirm_marketplace_delivery_for_milestone(first["id"])
    assert fake_alif.confirmed == ["sub-a", "sub-b"]
    assert await _tx_status(db_conn, held) == "ok"
    assert await _tx_status(db_conn, other) == "to_approve"
    assert await _tx_status(db_conn, done) == "ok"

    await service.confirm_marketplace_delivery_for_milestone(first["id"])
    assert fake_alif.confirmed == ["sub-a", "sub-b"]


async def test_confirm_for_contract_skips_confirmed_transactions(db_conn, fake_alif):  # noqa: F811
    milestone = await _funded_milestone(db_conn)
    held = await _insert_held_transaction(db_conn, milestone, status="to_approve")
    confirmed = await _insert_held_transaction(db_conn, milestone, status="ok")
    service = PaymentService(db_conn)

    await service.confirm_marketplace_deliveries_for_contract(milestone["contract_id"])
    await service.confirm_marketplace_deliveries_for_contract(milestone["contract_id"])

    assert fake_alif.confirmed == ["sub-a", "sub-b"]
    assert await _tx_status(db_conn, held) == "ok"
    assert await _tx_status(db_conn, confirmed) == "ok"


async def test_confirm_refreshes_empty_sub_transactions_via_checktxn(db_conn, fake_alif):  # noqa: F811
    milestone = await _funded_milestone(db_conn)
    order_id = await _insert_held_transaction(
        db_conn,
        milestone,
        status="to_approve",
        meta={"mpTerminalInfo": MARKETPLACE_META["mpTerminalInfo"]},
    )
    fake_alif.checktxn_by_order[order_id] = {
        "status": "to_approve",
        "sub_transactions": MARKETPLACE_META["sub_transactions"],
    }
    service = PaymentService(db_conn)

    await service.confirm_marketplace_delivery_for_milestone(milestone["id"])

    assert fake_alif.confirmed == ["sub-a", "sub-b"]
    assert await _tx_status(db_conn, order_id) == "ok"
    meta = await db_conn.fetchval(
        "SELECT marketplace_meta FROM payment_gateway_transactions WHERE order_id = $1",
        order_id,
    )
    if isinstance(meta, str):
        meta = json.loads(meta)
    assert len(meta.get("sub_transactions") or []) == 2


async def test_confirm_fails_when_sub_transactions_missing(db_conn, fake_alif):  # noqa: F811
    milestone = await _funded_milestone(db_conn)
    order_id = await _insert_held_transaction(
        db_conn,
        milestone,
        status="to_approve",
        meta={"mpTerminalInfo": MARKETPLACE_META["mpTerminalInfo"]},
    )
    service = PaymentService(db_conn)

    with pytest.raises(ConflictError, match="нет подтранзакций"):
        await service.confirm_marketplace_delivery_for_milestone(milestone["id"])

    assert fake_alif.confirmed == []
    assert await _tx_status(db_conn, order_id) == "to_approve"


async def test_cancel_marketplace_raises_and_keeps_failed_transactions(db_conn, fake_alif):  # noqa: F811
    milestone = await _create_awaiting_milestone(db_conn)
    failing = await _insert_held_transaction(
        db_conn,
        milestone,
        status="to_approve",
        meta={
            "sub_transactions": [
                {"transaction_id": "bad-1", "terminal_id": "610184"},
                {"transaction_id": "good-1", "terminal_id": "841418"},
            ],
            "mpTerminalInfo": MARKETPLACE_META["mpTerminalInfo"],
        },
    )
    succeeding = await _insert_held_transaction(db_conn, milestone, status="to_approve")
    already = await _insert_held_transaction(db_conn, milestone, status="canceled")
    fake_alif.fail_cancel = {"bad-1"}
    service = PaymentService(db_conn)

    with pytest.raises(AlifGatewayError):
        await service.cancel_marketplace_for_contract(milestone["contract_id"])

    assert await _tx_status(db_conn, failing) == "to_approve"
    assert await _tx_status(db_conn, succeeding) == "canceled"
    assert await _tx_status(db_conn, already) == "canceled"
    assert sorted(fake_alif.canceled) == ["good-1", "sub-a", "sub-b"]

    fake_alif.fail_cancel = set()
    await service.cancel_marketplace_for_contract(milestone["contract_id"])
    assert await _tx_status(db_conn, failing) == "canceled"
    assert sorted(fake_alif.canceled) == ["bad-1", "good-1", "sub-a", "sub-b"]


async def _released_supplier_balance(conn, amount: float):
    milestone = await _create_awaiting_milestone(conn, amount=amount)
    await conn.execute(
        "UPDATE payment_milestones SET status = 'released', commission_amount = 0 WHERE id = $1",
        milestone["id"],
    )
    supplier_actor_id = await conn.fetchval(
        "SELECT supplier_actor_id FROM contracts WHERE id = $1", milestone["contract_id"]
    )
    destination_id = await conn.fetchval(
        """
        INSERT INTO withdrawal_destinations (actor_id, type, label, details, is_default)
        VALUES ($1, 'bank', 'test', 'test', false)
        RETURNING id
        """,
        supplier_actor_id,
    )
    await conn.execute("DELETE FROM withdrawals WHERE actor_id = $1", supplier_actor_id)
    return supplier_actor_id, destination_id


async def test_withdrawal_cannot_exceed_balance_and_takes_advisory_lock(db_conn):  # noqa: F811
    supplier_actor_id, destination_id = await _released_supplier_balance(db_conn, 100.0)
    balance = await PaymentService(db_conn).supplier_balance(supplier_actor_id)
    service = SupplierFinanceService(db_conn)
    first_amount = balance.available * 0.6

    first = await service.request_withdrawal(
        supplier_actor_id,
        WithdrawalCreate(destination_id=destination_id, amount=first_amount, bank="alif"),
    )
    assert first.status == "pending"
    assert first.bank == "alif"
    with pytest.raises(ConflictError):
        await service.request_withdrawal(
            supplier_actor_id,
            WithdrawalCreate(
                destination_id=destination_id,
                amount=balance.available * 0.6,
                bank="alif",
            ),
        )

    count = await db_conn.fetchval(
        "SELECT COUNT(*) FROM withdrawals WHERE actor_id = $1", supplier_actor_id
    )
    assert count == 1
    held_locks = await db_conn.fetchval(
        "SELECT COUNT(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
    )
    assert held_locks >= 1


async def test_withdrawal_rejects_unavailable_banks(db_conn):  # noqa: F811
    supplier_actor_id, destination_id = await _released_supplier_balance(db_conn, 50.0)
    service = SupplierFinanceService(db_conn)
    for bank in ("dc", "eskhata"):
        with pytest.raises(ConflictError, match="пока недоступен"):
            await service.request_withdrawal(
                supplier_actor_id,
                WithdrawalCreate(destination_id=destination_id, amount=10.0, bank=bank),
            )
    count = await db_conn.fetchval(
        "SELECT COUNT(*) FROM withdrawals WHERE actor_id = $1", supplier_actor_id
    )
    assert count == 0
