import pytest

from tests.sql import execute, fetchrow

API = "/api/v1"


@pytest.fixture
async def supplier_ids(client, admin_auth, supplier_auth):
    """Seed supplier's user id and actor id; restores plan and mark after the test."""
    user_id = supplier_auth["me"]["user"]["id"]
    actor_id = supplier_auth["actor_id"]
    previous = await fetchrow(
        "SELECT plan::text AS plan, active_until FROM supplier_subscriptions WHERE user_id = $1",
        user_id,
    )
    yield user_id, actor_id
    await execute(
        "UPDATE actors SET reputation_override = NULL, reputation_override_reason = NULL WHERE id = $1",
        actor_id,
    )
    if previous:
        await execute(
            "UPDATE supplier_subscriptions SET plan = $2::subscription_plan, active_until = $3 WHERE user_id = $1",
            user_id,
            previous["plan"],
            previous["active_until"],
        )
    else:
        await execute("DELETE FROM supplier_subscriptions WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_admin_grants_and_removes_a_plan(client, admin_auth, supplier_ids):
    user_id, actor_id = supplier_ids
    res = await client.put(
        f"{API}/admin/users/{user_id}/subscription",
        json={"plan": "business", "months": 3, "reason": "Партнёр"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    assert res.json()["plan"] == "business" and res.json()["is_active"] is True

    public = (await client.get(f"{API}/public/suppliers/{actor_id}")).json()
    assert public["subscription_plan"] == "business"

    res = await client.put(
        f"{API}/admin/users/{user_id}/subscription",
        json={"plan": "none"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False
    public = (await client.get(f"{API}/public/suppliers/{actor_id}")).json()
    assert public["subscription_plan"] is None


@pytest.mark.asyncio
async def test_admin_pins_removes_and_resets_the_mark(client, admin_auth, supplier_ids):
    user_id, actor_id = supplier_ids
    computed = (await client.get(f"{API}/public/suppliers/{actor_id}")).json()["reputation_tier"]

    res = await client.put(
        f"{API}/admin/suppliers/{actor_id}/reputation",
        json={"tier": "diamond", "reason": "Проверенный партнёр"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    assert res.json()["tier"] == "diamond"
    assert res.json()["tier_auto"] == computed
    assert (await client.get(f"{API}/public/suppliers/{actor_id}")).json()["reputation_tier"] == "diamond"

    res = await client.put(
        f"{API}/admin/suppliers/{actor_id}/reputation",
        json={"tier": "none"},
        headers=admin_auth["headers"],
    )
    assert res.json()["tier"] == "none"
    assert (await client.get(f"{API}/public/suppliers/{actor_id}")).json()["reputation_tier"] == "none"

    res = await client.put(
        f"{API}/admin/suppliers/{actor_id}/reputation",
        json={"tier": "auto"},
        headers=admin_auth["headers"],
    )
    assert res.json()["tier_override"] is None
    assert (await client.get(f"{API}/public/suppliers/{actor_id}")).json()["reputation_tier"] == computed

    marks = await client.get(f"{API}/admin/users/{user_id}/marks", headers=admin_auth["headers"])
    assert marks.status_code == 200, marks.text
    assert actor_id in [s["actor_id"] for s in marks.json()["suppliers"]]


@pytest.mark.asyncio
async def test_only_admins_set_marks(client, buyer_auth, supplier_ids):
    user_id, actor_id = supplier_ids
    res = await client.put(
        f"{API}/admin/users/{user_id}/subscription",
        json={"plan": "pro"},
        headers=buyer_auth["headers"],
    )
    assert res.status_code in (401, 403)
    res = await client.put(
        f"{API}/admin/suppliers/{actor_id}/reputation",
        json={"tier": "gold"},
        headers=buyer_auth["headers"],
    )
    assert res.status_code in (401, 403)
