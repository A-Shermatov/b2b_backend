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
    filtered = await client.get(
        f"{API}/supplier/proposals/",
        headers=supplier_auth["headers"],
        params={"status": "submitted"},
    )
    assert filtered.status_code == 200
    assert all(item["status"] == "submitted" for item in filtered.json()["items"])
    pending = await client.get(
        f"{API}/supplier/proposals/",
        headers=supplier_auth["headers"],
        params={"status": "pending_supplier"},
    )
    assert pending.status_code == 200, pending.text
    assert all(item["status"] == "pending_supplier" for item in pending.json()["items"])
    invalid = await client.get(
        f"{API}/supplier/proposals/",
        headers=supplier_auth["headers"],
        params={"status": "unknown"},
    )
    assert invalid.status_code == 422


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
async def test_supplier_create_company(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.post(
        f"{API}/supplier/companies/",
        headers=supplier_auth["headers"],
        json={
            "title": "Supplier Co Create",
            "legal_name": "Supplier Co Create LLC",
            "tax_number": "987654321",
            "description": "Supplier company",
            "country": "Таджикистан",
            "city": "Душанбе",
            "address": "ул. Поставщик, 2",
            "website": "https://supplier.example.com",
            "founded_year": "2019",
            "employees_count": "25",
            "languages": ["ru", "tg"],
            "industries": ["Logistics"],
            "category_ids": [],
            "certificates": [],
            "team": [],
            "actor_types": ["supplier"],
        },
    )
    assert res.status_code == 200, res.text
    company = res.json()
    assert company["title"] == "Supplier Co Create"
    assert "supplier" in company["actor_types"]

    get_res = await client.get(
        f"{API}/supplier/companies/{company['id']}",
        headers=supplier_auth["headers"],
    )
    assert get_res.status_code == 200
    assert get_res.json()["id"] == company["id"]


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
