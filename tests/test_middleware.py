import pytest

API = "/api/v1"


@pytest.mark.asyncio
async def test_get_does_not_refresh_access_token(client, buyer_auth):
    res = await client.get(f"{API}/auth/me", headers=buyer_auth["headers"])
    assert res.status_code == 200, res.text
    assert "x-access-token" not in {k.lower() for k in res.headers}


@pytest.mark.asyncio
async def test_mutating_request_refreshes_access_token(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("Missing buyer actor")
    res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": "Middleware refresh RFQ",
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "draft",
            "project_duration": "1 month",
            "start_date": "2026-10-01",
        },
    )
    assert res.status_code == 200, res.text
    token = res.headers.get("x-access-token")
    assert token
    assert res.headers.get("authorization") == f"Bearer {token}"
