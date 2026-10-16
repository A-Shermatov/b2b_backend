import pytest

API = "/api/v1"


@pytest.mark.asyncio
async def test_supplier_payment_balance(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/payments/balance",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "available" in data
    assert "pending" in data
    assert "escrow_locked" in data


@pytest.mark.asyncio
async def test_supplier_payment_history(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/payments/history",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert "total" in body and "total_pages" in body


@pytest.mark.asyncio
async def test_supplier_list_proposals(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/proposals/",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert "total" in body and "total_pages" in body


@pytest.mark.asyncio
async def test_supplier_companies_me(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/companies/me",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert "total" in body and "total_pages" in body


@pytest.mark.asyncio
async def test_supplier_subscription_get(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/subscription",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "plan" in data
    assert "is_active" in data


@pytest.mark.asyncio
async def test_supplier_catalog_list(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/catalog/items",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert "total" in body and "total_pages" in body


@pytest.mark.asyncio
async def test_supplier_rfqs_list(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.get(
        f"{API}/supplier/rfqs/board",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert "total" in body and "total_pages" in body
