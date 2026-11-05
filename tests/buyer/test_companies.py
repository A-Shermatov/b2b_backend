import pytest

API = "/api/v1"


def _wizard_payload(title: str) -> dict:
    return {
        "title": title,
        "legal_name": f"{title} LLC",
        "tax_number": "123456789",
        "description": "Test company",
        "logo": "",
        "country": "Таджикистан",
        "city": "Душанбе",
        "address": "ул. Тест, 1",
        "website": "https://example.com",
        "founded_year": "2020",
        "employees_count": "10",
        "annual_revenue_range": "",
        "languages": ["ru"],
        "industries": ["IT"],
        "category_ids": [],
        "certificates": [],
        "team": [],
        "actor_types": ["buyer"],
    }


@pytest.mark.asyncio
async def test_buyer_create_and_get_company(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")

    create_res = await client.post(
        f"{API}/buyer/companies/",
        headers=buyer_auth["headers"],
        json=_wizard_payload("Buyer Co Create"),
    )
    assert create_res.status_code == 200, create_res.text
    company = create_res.json()
    assert company["title"] == "Buyer Co Create"
    assert company["verification_status"] == "pending"
    assert company["owner_id"]
    assert any(u["role"] == "director" for u in company["company_users"])

    get_res = await client.get(
        f"{API}/buyer/companies/{company['id']}",
        headers=buyer_auth["headers"],
    )
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["id"] == company["id"]

    # The shared demo buyer accumulates companies across runs: look through all pages.
    found = False
    page = 1
    while not found:
        me_res = await client.get(
            f"{API}/buyer/companies/me",
            params={"page": page, "page_size": 100},
            headers=buyer_auth["headers"],
        )
        assert me_res.status_code == 200
        data = me_res.json()
        found = any(item["id"] == company["id"] for item in data["items"])
        if page >= data.get("total_pages", 1):
            break
        page += 1
    assert found


@pytest.mark.asyncio
async def test_buyer_company_certificates_crud(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")

    create_res = await client.post(
        f"{API}/buyer/companies/",
        headers=buyer_auth["headers"],
        json=_wizard_payload("Buyer Co Certs"),
    )
    assert create_res.status_code == 200, create_res.text
    company_id = create_res.json()["id"]

    add_res = await client.post(
        f"{API}/buyer/companies/{company_id}/certificates",
        headers=buyer_auth["headers"],
        json={
            "title": "ISO 9001",
            "issuer": "Cert Body",
            "issue_date": "2024-01-01",
            "expiry_date": "2027-01-01",
            "file_url": "certificates/test.pdf",
        },
    )
    assert add_res.status_code == 200, add_res.text
    cert_id = add_res.json()["id"]

    patch_res = await client.patch(
        f"{API}/buyer/companies/{company_id}/certificates/{cert_id}",
        headers=buyer_auth["headers"],
        json={"title": "ISO 9001:2015"},
    )
    assert patch_res.status_code == 200, patch_res.text
    assert patch_res.json()["title"] == "ISO 9001:2015"

    delete_res = await client.delete(
        f"{API}/buyer/companies/{company_id}/certificates/{cert_id}",
        headers=buyer_auth["headers"],
    )
    assert delete_res.status_code == 204, delete_res.text

    missing = await client.delete(
        f"{API}/buyer/companies/{company_id}/certificates/{cert_id}",
        headers=buyer_auth["headers"],
    )
    assert missing.status_code == 404
