import pytest

from app.modules.trust.party import party_tier
from tests.conftest import _login
from tests.sql import execute, fetch, fetchrow

API = "/api/v1"


def _row(**values):
    base = {
        "party_completed": 0,
        "party_disputed": 0,
        "party_clean_completed": 0,
        "party_reviews": 0,
        "party_rating": 0.0,
        "party_override": None,
    }
    return {**base, **values}


def test_deals_in_both_roles_add_up():
    # 6 sold + 5 bought: neither role alone reaches silver (10), together they do.
    row = _row(party_completed=11, party_clean_completed=11, party_reviews=8, party_rating=4.8)
    assert party_tier(row, "verified") == "silver"


def test_a_lost_dispute_in_either_role_counts():
    row = _row(party_completed=40, party_clean_completed=38, party_disputed=2, party_reviews=20, party_rating=4.9)
    # 38 / 40 = 95% without disputes: gold, not diamond-level deals anyway.
    assert party_tier(row, "verified") == "gold"
    # 37 / 40 = 92.5%: below gold's 95%, still silver.
    row["party_disputed"] = 3
    row["party_clean_completed"] = 37
    assert party_tier(row, "verified") == "silver"
    # 35 / 40 = 87.5%: below silver's 90%.
    row["party_disputed"] = 5
    row["party_clean_completed"] = 35
    assert party_tier(row, "verified") == "none"


def test_admin_mark_wins_and_unverified_gets_nothing():
    assert party_tier(_row(party_override="diamond"), "pending") == "diamond"
    assert party_tier(_row(party_completed=200, party_clean_completed=200, party_reviews=50, party_rating=5), "pending") == "none"


def test_rows_without_party_columns_fall_back():
    assert party_tier({"party_completed": None}, "verified") is None


@pytest.fixture
async def both_auth(client):
    return await _login(client, "both@example.com", "Both123!", side="supplier")


async def _clear_marks(actor_ids):
    await execute(
        """
        UPDATE actors SET reputation_override = NULL, reputation_override_reason = NULL,
                          reputation_override_by = NULL, reputation_override_at = NULL
        WHERE id = ANY($1::int[])
        """,
        list(actor_ids),
    )


@pytest.mark.asyncio
async def test_mark_and_plan_show_on_every_profile_of_the_owner(client, admin_auth, both_auth):
    """The seed owner buys through one company and sells through another, and has own profiles."""
    me = both_auth["me"]
    user_id = me["user"]["id"]
    profiles = {a["id"]: a["side"] for a in me["actors"]}
    if set(profiles.values()) < {"supplier", "buyer"}:
        pytest.skip("The seed account has no buying and selling profile pair")
    supplier_id = next(i for i, side in profiles.items() if side == "supplier")
    previous = await fetchrow(
        "SELECT plan::text AS plan, active_until FROM supplier_subscriptions WHERE user_id = $1", user_id
    )
    try:
        res = await client.put(
            f"{API}/admin/suppliers/{supplier_id}/reputation",
            json={"tier": "gold", "reason": "Проверка"},
            headers=admin_auth["headers"],
        )
        assert res.status_code == 200, res.text
        mark = res.json()
        assert set(mark["sides"]) == {"supplier", "buyer"}
        assert {p["actor_id"] for p in mark["profiles"]} >= set(profiles)
        res = await client.put(
            f"{API}/admin/users/{user_id}/subscription",
            json={"plan": "pro", "months": 1},
            headers=admin_auth["headers"],
        )
        assert res.status_code == 200, res.text

        for actor_id, side in profiles.items():
            path = "suppliers" if side == "supplier" else "buyers"
            public = await client.get(f"{API}/public/{path}/{actor_id}")
            if public.status_code == 404:
                continue  # an inactive or hidden profile
            assert public.json()["reputation_tier"] == "gold", (actor_id, side)
            assert public.json()["subscription_plan"] == "pro", (actor_id, side)

        # Removing the mark from the buying side removes it everywhere.
        buyer_id = next(i for i, side in profiles.items() if side == "buyer")
        res = await client.put(
            f"{API}/admin/suppliers/{buyer_id}/reputation",
            json={"tier": "none"},
            headers=admin_auth["headers"],
        )
        assert res.status_code == 200, res.text
        assert res.json()["tier"] == "none"
        selling = (await client.get(f"{API}/public/suppliers/{supplier_id}")).json()
        assert selling["reputation_tier"] == "none"

        marks = (await client.get(f"{API}/admin/users/{user_id}/marks", headers=admin_auth["headers"])).json()
        assert len(marks["suppliers"]) == 1
    finally:
        await _clear_marks(profiles)
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
async def test_supplier_sees_the_buyer_marks_in_an_rfq(client, admin_auth, buyer_auth, supplier_auth):
    from tests.buyer.test_rfq_integrity import _create_rfq

    rfq_id = await _create_rfq(client, buyer_auth, "Party marks in RFQ", status="published")
    buyer_actor = buyer_auth["actor_id"]
    try:
        res = await client.put(
            f"{API}/admin/suppliers/{buyer_actor}/reputation",
            json={"tier": "diamond"},
            headers=admin_auth["headers"],
        )
        assert res.status_code == 200, res.text
        rfq = await client.get(f"{API}/supplier/rfqs/{rfq_id}", headers=supplier_auth["headers"])
        assert rfq.status_code == 200, rfq.text
        assert rfq.json()["buyer"]["reputation_tier"] == "diamond"
        assert "subscription_plan" in rfq.json()["buyer"]
    finally:
        owner = await fetchrow(
            "SELECT COALESCE(c.owner_id, a.user_id) AS id FROM actors a LEFT JOIN companies c ON c.id = a.company_id WHERE a.id = $1",
            buyer_actor,
        )
        rows = await fetch(
            "SELECT a.id FROM actors a LEFT JOIN companies c ON c.id = a.company_id WHERE COALESCE(c.owner_id, a.user_id) = $1",
            owner["id"],
        )
        await _clear_marks([r["id"] for r in rows] + [buyer_actor])
