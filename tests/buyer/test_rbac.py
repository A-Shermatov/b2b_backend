import pytest

API = "/api/v1"


@pytest.mark.asyncio
async def test_buyer_can_list_rfqs(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor in seed data")
    res = await client.get(f"{API}/buyer/rfqs/", headers=buyer_auth["headers"])
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_supplier_cannot_access_buyer_rfqs(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor in seed data")
    res = await client.get(f"{API}/buyer/rfqs/", headers=supplier_auth["headers"])
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_buyer_cannot_access_supplier_board(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor in seed data")
    res = await client.get(f"{API}/supplier/rfqs/board", headers=buyer_auth["headers"])
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_supplier_can_access_board(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor in seed data")
    res = await client.get(f"{API}/supplier/rfqs/board", headers=supplier_auth["headers"])
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_supplier_can_list_own_proposals(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor in seed data")
    res = await client.get(f"{API}/supplier/proposals/", headers=supplier_auth["headers"])
    assert res.status_code == 200
    assert isinstance(res.json()["items"], list)


@pytest.mark.asyncio
async def test_unauthenticated_buyer_rfqs_returns_401(client):
    res = await client.get(f"{API}/buyer/rfqs/")
    assert res.status_code == 401
