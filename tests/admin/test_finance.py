import pytest

from app.modules.finance.services import FinanceLedgerService
from tests.sql import fetchrow

API = "/api/v1"


async def _seed_payment(
    *,
    payment_type: str,
    status: str,
    title: str,
    amount: float = 1000.0,
    commission: float = 50.0,
    actor_id: int | None = None,
) -> int:
    payment = await FinanceLedgerService().record(
        payment_type=payment_type,
        amount=amount,
        commission=commission,
        title=title,
        status=status,
        gateway="mock",
        actor_id=actor_id,
        currency="TJS",
    )
    return payment["id"]


@pytest.mark.asyncio
async def test_admin_finance_list_and_detail(client, admin_auth, buyer_auth):
    payment_id = await _seed_payment(
        payment_type="platform_revenue",
        status="paid",
        title="Revenue finance list",
        actor_id=buyer_auth.get("actor_id"),
    )

    list_res = await client.get(
        f"{API}/admin/finance",
        params={"view": "platform_revenue", "query": "Revenue finance", "page_size": 10},
        headers=admin_auth["headers"],
    )
    assert list_res.status_code == 200, list_res.text
    data = list_res.json()
    assert data["view_counts"]["platform_revenue"] >= 1
    assert any(item["id"] == payment_id for item in data["items"])

    detail_res = await client.get(
        f"{API}/admin/finance/{payment_id}",
        headers=admin_auth["headers"],
    )
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["title"] == "Revenue finance list"
    assert detail["gateway"] == "mock"
    assert detail["commission"] == 50.0
    assert isinstance(detail["history"], list)


@pytest.mark.asyncio
async def test_admin_finance_actions(client, admin_auth, buyer_auth):
    actor_id = buyer_auth.get("actor_id")
    mark_id = await _seed_payment(
        payment_type="payout",
        status="pending",
        title="Mark paid payout",
        actor_id=actor_id,
    )
    retry_id = await _seed_payment(
        payment_type="subscription",
        status="failed",
        title="Retry subscription",
        actor_id=actor_id,
    )
    refund_id = await _seed_payment(
        payment_type="commission",
        status="paid",
        title="Refund commission",
        actor_id=actor_id,
    )

    mark_res = await client.post(
        f"{API}/admin/finance/{mark_id}/action",
        json={"action": "mark_paid"},
        headers=admin_auth["headers"],
    )
    assert mark_res.status_code == 200, mark_res.text
    assert mark_res.json()["status"] == "paid"

    retry_res = await client.post(
        f"{API}/admin/finance/{retry_id}/action",
        json={"action": "retry", "reason": "Повтор после сбоя шлюза"},
        headers=admin_auth["headers"],
    )
    assert retry_res.status_code == 200, retry_res.text
    assert retry_res.json()["status"] == "paid"

    refund_res = await client.post(
        f"{API}/admin/finance/{refund_id}/action",
        json={"action": "refund", "reason": "Ошибочное списание"},
        headers=admin_auth["headers"],
    )
    assert refund_res.status_code == 200, refund_res.text
    assert refund_res.json()["status"] == "refunded"

    payment = await fetchrow(
        "SELECT status::text AS status FROM platform_payments WHERE id = $1",
        refund_id,
    )
    assert payment is not None
    assert payment["status"] == "refunded"


@pytest.mark.asyncio
async def test_admin_finance_export_csv(client, admin_auth):
    await _seed_payment(
        payment_type="platform_revenue",
        status="paid",
        title="Export revenue row",
    )
    export_res = await client.get(
        f"{API}/admin/finance/export",
        params={"view": "platform_revenue"},
        headers=admin_auth["headers"],
    )
    assert export_res.status_code == 200, export_res.text
    assert "text/csv" in export_res.headers.get("content-type", "")
    body = export_res.text
    assert body.startswith("id,type,status,gateway,amount,commission,currency,title,invoice_id,created_at")
    assert "Export revenue row" in body


@pytest.mark.asyncio
async def test_buyer_cannot_access_admin_finance(client, buyer_auth):
    if not buyer_auth.get("actor_id"):
        pytest.skip("No buyer actor")
    res = await client.get(
        f"{API}/admin/finance",
        headers=buyer_auth["headers"],
    )
    assert res.status_code == 403
