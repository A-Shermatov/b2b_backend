import pytest

from tests.staff import (
    create_role_with_permissions,
    create_staff,
    login_headers,
    unique_suffix,
)


@pytest.mark.asyncio
async def test_merchant_balances_for_admin(client, admin_auth):
    response = await client.get(
        "/api/v1/admin/merchant-balances",
        headers=admin_auth["headers"],
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "ledger"
    assert data["note"]
    assert len(data["merchants"]) == 2
    roles = {item["role"] for item in data["merchants"]}
    assert roles == {"supplier_pool", "platform"}
    for merchant in data["merchants"]:
        assert merchant["held"] >= 0
        assert merchant["released"] >= 0
        assert merchant["refunded"] >= 0
        assert merchant["net_released"] >= 0
        assert "configured" in merchant
    assert {"held", "released", "refunded", "supplier_available", "withdrawn", "currency"} <= set(
        data["totals"]
    )
    supplier = next(m for m in data["merchants"] if m["role"] == "supplier_pool")
    platform = next(m for m in data["merchants"] if m["role"] == "platform")
    assert data["totals"]["held"] == pytest.approx(
        supplier["held"] + platform["held"], abs=0.01
    )
    assert data["totals"]["released"] == pytest.approx(
        supplier["released"] + platform["released"], abs=0.01
    )
    assert {"items", "total", "page", "page_size", "total_pages"} <= set(data["suppliers"])


@pytest.mark.asyncio
async def test_merchant_balances_search_suppliers(client, admin_auth):
    response = await client.get(
        "/api/v1/admin/merchant-balances",
        params={"query": "zzz-no-such-supplier", "page_size": 5},
        headers=admin_auth["headers"],
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["suppliers"]["total"] == 0
    assert data["suppliers"]["items"] == []


@pytest.mark.asyncio
async def test_merchant_balances_denied_without_finance_view(client, admin_auth):
    role_id = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"no-finance-{unique_suffix()}",
        ["admin_dashboard.view"],
    )
    staff = await create_staff(client, admin_auth["headers"], role_id)
    headers = await login_headers(client, staff["email"])
    response = await client.get("/api/v1/admin/merchant-balances", headers=headers)
    assert response.status_code == 403, response.text
