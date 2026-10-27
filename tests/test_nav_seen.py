import pytest

API = "/api/v1"


@pytest.mark.asyncio
async def test_supplier_nav_seen_clears_new_rfqs(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    headers = supplier_auth["headers"]
    before = await client.get(f"{API}/supplier/nav-counts", headers=headers)
    assert before.status_code == 200, before.text
    body = before.json()
    assert "rfqs" in body and "rfqs_new" in body
    assert body["rfqs_new"] == body["rfqs"]

    seen = await client.post(
        f"{API}/supplier/nav-seen",
        headers=headers,
        json={"section": "rfqs"},
    )
    assert seen.status_code == 200, seen.text

    after = await client.get(f"{API}/supplier/nav-counts", headers=headers)
    assert after.status_code == 200, after.text
    data = after.json()
    assert data["rfqs"] == body["rfqs"]
    assert data["rfqs_new"] == 0


@pytest.mark.asyncio
async def test_buyer_nav_seen_rejects_unknown_section(client, buyer_auth):
    if not buyer_auth.get("actor_id"):
        pytest.skip("No buyer actor")
    res = await client.post(
        f"{API}/buyer/nav-seen",
        headers=buyer_auth["headers"],
        json={"section": "catalog"},
    )
    assert res.status_code == 422
