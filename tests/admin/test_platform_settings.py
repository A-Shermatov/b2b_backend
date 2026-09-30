import uuid

import pytest

from app.modules.settings.service import invalidate_platform_settings_cache
from tests.sql import execute, fetch, fetchrow, upsert_moderator

API = "/api/v1"


@pytest.fixture
async def restore_settings(client):
    rows = await fetch("SELECT key, value FROM platform_settings")
    yield
    for row in rows:
        await execute(
            "UPDATE platform_settings SET value = $2::jsonb WHERE key = $1",
            row["key"],
            row["value"],
        )
    invalidate_platform_settings_cache()


async def _put_settings(client, admin_auth, **values):
    payload = {"commission_percent": 5, "commission_min": 0, "max_contract_amount": None, **values}
    res = await client.put(f"{API}/admin/settings", json=payload, headers=admin_auth["headers"])
    assert res.status_code == 200, res.text
    return res.json()


async def _moderator_headers(client) -> dict:
    await upsert_moderator()
    res = await client.post(
        f"{API}/auth/login",
        json={"email": "moderator@example.com", "password": "Moderator123!"},
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _create_proposal(client, buyer_auth, supplier_auth, price: float, title: str) -> int:
    rfq_res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": title,
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "draft",
            "project_duration": "2 months",
            "start_date": "2026-07-01",
        },
    )
    assert rfq_res.status_code == 200, rfq_res.text
    rfq_id = rfq_res.json()["id"]
    publish_res = await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_auth["headers"])
    assert publish_res.status_code == 200, publish_res.text
    proposal_res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={"price": price, "currency": "TJS", "delivery_time": "14 days", "message": "Готовы"},
    )
    assert proposal_res.status_code == 200, proposal_res.text
    return proposal_res.json()["id"]


async def _accept(client, buyer_auth, proposal_id: int, payment_type: str):
    return await client.post(
        f"{API}/buyer/proposals/{proposal_id}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": payment_type},
    )


@pytest.mark.asyncio
async def test_settings_permissions_and_validation(client, admin_auth, restore_settings):
    admin_res = await client.get(f"{API}/admin/settings", headers=admin_auth["headers"])
    assert admin_res.status_code == 200, admin_res.text
    assert admin_res.json()["can_update"] is True
    assert admin_res.json()["currency"] == "TJS"

    invalid = await client.put(
        f"{API}/admin/settings",
        json={"commission_percent": 150, "commission_min": 0, "max_contract_amount": None},
        headers=admin_auth["headers"],
    )
    assert invalid.status_code == 422

    saved = await _put_settings(client, admin_auth, commission_percent=7.5, commission_min=50)
    assert saved["commission_percent"] == 7.5
    assert saved["commission_min"] == 50

    public_res = await client.get(f"{API}/public/platform-settings")
    assert public_res.status_code == 200
    assert public_res.json()["commission_percent"] == 7.5

    audit = await fetchrow(
        "SELECT action FROM audit_logs WHERE resource_type = 'platform_settings' ORDER BY id DESC LIMIT 1"
    )
    assert audit["action"] == "platform_settings.update"

    moderator_headers = await _moderator_headers(client)
    mod_get = await client.get(f"{API}/admin/settings", headers=moderator_headers)
    assert mod_get.status_code == 200, mod_get.text
    assert mod_get.json()["can_update"] is False
    mod_put = await client.put(
        f"{API}/admin/settings",
        json={"commission_percent": 1, "commission_min": 0, "max_contract_amount": None},
        headers=moderator_headers,
    )
    assert mod_put.status_code == 403


@pytest.mark.asyncio
async def test_contract_limit_on_submit_and_accept(
    client, admin_auth, buyer_auth, supplier_auth, restore_settings
):
    await _put_settings(client, admin_auth, max_contract_amount=None)
    proposal_id = await _create_proposal(client, buyer_auth, supplier_auth, 15000, "Limit accept RFQ")

    await _put_settings(client, admin_auth, max_contract_amount=10000)
    accept_res = await _accept(client, buyer_auth, proposal_id, "full_prepayment")
    assert accept_res.status_code == 422
    assert "не может превышать" in accept_res.json()["detail"]

    rfq_res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": "Limit submit RFQ",
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "draft",
            "project_duration": "2 months",
            "start_date": "2026-07-01",
        },
    )
    rfq_id = rfq_res.json()["id"]
    await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_auth["headers"])
    submit_res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={"price": 15000, "currency": "TJS", "delivery_time": "14 days", "message": "Готовы"},
    )
    assert submit_res.status_code == 422
    assert "не может превышать" in submit_res.json()["detail"]


@pytest.mark.asyncio
async def test_commission_flow_split_release_and_balance(
    client, admin_auth, buyer_auth, supplier_auth, restore_settings
):
    await _put_settings(client, admin_auth, commission_percent=10, commission_min=2000)
    proposal_id = await _create_proposal(client, buyer_auth, supplier_auth, 15000, "Commission flow RFQ")
    accept_res = await _accept(client, buyer_auth, proposal_id, "split_payment")
    assert accept_res.status_code == 200, accept_res.text
    contract_id = accept_res.json()["contract_id"]

    contract_res = await client.get(f"{API}/buyer/contracts/{contract_id}", headers=buyer_auth["headers"])
    assert contract_res.status_code == 200, contract_res.text
    contract = contract_res.json()
    assert contract["commission_amount"] == 2000
    assert contract["commission_percent"] == 10
    assert contract["commission_min"] == 2000
    milestones = contract["payment_plan"]["milestones"]
    assert [m["commission_amount"] for m in milestones] == [1000, 1000]

    balance_before = (
        await client.get(f"{API}/supplier/payments/balance", headers=supplier_auth["headers"])
    ).json()["available"]

    first = milestones[0]
    fund_res = await client.post(
        f"{API}/buyer/payments/milestones/{first['id']}/fund",
        headers=buyer_auth["headers"],
    )
    assert fund_res.status_code == 200, fund_res.text

    escrow_res = await client.get(
        f"{API}/admin/escrow", params={"view": "held", "query": f"#{contract_id}"}, headers=admin_auth["headers"]
    )
    assert escrow_res.status_code == 200, escrow_res.text
    escrow_item = next(item for item in escrow_res.json()["items"] if item["id"] == contract_id)
    assert escrow_item["amount"] == 7500
    assert escrow_item["commission"] == 1000

    approve_res = await client.post(
        f"{API}/buyer/payments/milestones/{first['id']}/approve",
        headers=buyer_auth["headers"],
    )
    assert approve_res.status_code == 200, approve_res.text

    balance_after = (
        await client.get(f"{API}/supplier/payments/balance", headers=supplier_auth["headers"])
    ).json()["available"]
    assert balance_after - balance_before == pytest.approx(6500)

    commission_row = await fetchrow(
        """
        SELECT amount, description FROM platform_payments
        WHERE contract_id = $1 AND type::text = 'commission'
        """,
        contract_id,
    )
    assert commission_row["amount"] == 1000
    assert "10%, минимум 2000 TJS" in commission_row["description"]


@pytest.mark.asyncio
async def test_admin_categories_crud_and_guards(client, admin_auth):
    suffix = uuid.uuid4().hex[:8]
    parent_res = await client.post(
        f"{API}/admin/categories",
        json={"name": "Тест родитель", "slug": f"test-parent-{suffix}"},
        headers=admin_auth["headers"],
    )
    assert parent_res.status_code == 201, parent_res.text
    parent = parent_res.json()

    duplicate = await client.post(
        f"{API}/admin/categories",
        json={"name": "Дубликат", "slug": f"test-parent-{suffix}"},
        headers=admin_auth["headers"],
    )
    assert duplicate.status_code == 409

    bad_slug = await client.post(
        f"{API}/admin/categories",
        json={"name": "Плохой slug", "slug": "Плохой slug"},
        headers=admin_auth["headers"],
    )
    assert bad_slug.status_code == 422

    child_res = await client.post(
        f"{API}/admin/categories",
        json={"name": "Тест ребёнок", "slug": f"test-child-{suffix}", "parent_id": parent["id"]},
        headers=admin_auth["headers"],
    )
    assert child_res.status_code == 201, child_res.text
    child = child_res.json()

    cycle = await client.patch(
        f"{API}/admin/categories/{parent['id']}",
        json={"parent_id": child["id"]},
        headers=admin_auth["headers"],
    )
    assert cycle.status_code == 422

    renamed = await client.patch(
        f"{API}/admin/categories/{child['id']}",
        json={"name": "Тест переименован"},
        headers=admin_auth["headers"],
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Тест переименован"
    assert renamed.json()["parent_id"] == parent["id"]

    list_res = await client.get(f"{API}/admin/categories", headers=admin_auth["headers"])
    listed_parent = next(item for item in list_res.json()["items"] if item["id"] == parent["id"])
    assert listed_parent["children_count"] == 1

    blocked = await client.delete(f"{API}/admin/categories/{parent['id']}", headers=admin_auth["headers"])
    assert blocked.status_code == 409
    assert "подкатегорий" in blocked.json()["detail"]

    assert (await client.delete(f"{API}/admin/categories/{child['id']}", headers=admin_auth["headers"])).status_code == 200
    assert (await client.delete(f"{API}/admin/categories/{parent['id']}", headers=admin_auth["headers"])).status_code == 200


@pytest.mark.asyncio
async def test_admin_analytics_periods(client, admin_auth):
    for period, expected_bucket in (("30d", "day"), ("90d", "week"), ("12m", "month")):
        res = await client.get(f"{API}/admin/analytics", params={"period": period}, headers=admin_auth["headers"])
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["bucket"] == expected_bucket
        assert data["series"], period
        assert set(data["totals"]) == {
            "users", "companies", "rfqs", "contracts", "contract_volume", "released", "commission",
        }
    daily = (
        await client.get(f"{API}/admin/analytics", params={"period": "30d"}, headers=admin_auth["headers"])
    ).json()
    assert len(daily["series"]) == 30
