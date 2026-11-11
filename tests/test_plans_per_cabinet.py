"""A paid plan belongs to one cabinet, and what a subscriber posts stands first."""

import secrets

import pytest

from tests.buyer.test_deadlines_selfdeal import _rfq_payload, _submit
from tests.conftest import _login
from tests.sql import execute, fetch, fetchval
from tests.test_audit_regressions import _actor, _headers, _register

API = "/api/v1"


async def _set_plan(user_id: int, side: str, plan: str) -> None:
    await execute(
        """
        INSERT INTO supplier_subscriptions (user_id, side, plan, active_until, created_at)
        VALUES ($1, $2, $3::subscription_plan, NOW() + INTERVAL '30 days', NOW())
        ON CONFLICT (user_id, side) DO UPDATE
        SET plan = EXCLUDED.plan, active_until = EXCLUDED.active_until
        """,
        user_id,
        side,
        plan,
    )


class _plans_kept:
    """Put the user's plans back as they were, whatever the test did to them."""

    def __init__(self, *user_ids: int):
        self.user_ids = user_ids
        self.rows: list = []

    async def __aenter__(self):
        self.rows = await fetch(
            "SELECT user_id, side, plan::text AS plan, active_until FROM supplier_subscriptions "
            "WHERE user_id = ANY($1::bigint[])",
            list(self.user_ids),
        )
        await execute("DELETE FROM supplier_subscriptions WHERE user_id = ANY($1::bigint[])", list(self.user_ids))
        return self

    async def __aexit__(self, *_exc):
        await execute("DELETE FROM supplier_subscriptions WHERE user_id = ANY($1::bigint[])", list(self.user_ids))
        for row in self.rows:
            await execute(
                "INSERT INTO supplier_subscriptions (user_id, side, plan, active_until, created_at) "
                "VALUES ($1, $2, $3::subscription_plan, $4, NOW())",
                row["user_id"],
                row["side"],
                row["plan"],
                row["active_until"],
            )


async def _publish(client, headers: dict, title: str) -> str:
    res = await client.post(f"{API}/buyer/rfqs/", headers=headers, json=_rfq_payload(title))
    assert res.status_code == 200, res.text
    rfq_id = res.json()["id"]
    res = await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=headers)
    assert res.status_code == 200, res.text
    return rfq_id


@pytest.mark.asyncio
async def test_buyer_cabinet_has_a_plan_of_its_own(client, buyer_auth):
    user_id = buyer_auth["me"]["user"]["id"]
    async with _plans_kept(user_id):
        res = await client.get(f"{API}/buyer/subscription", headers=buyer_auth["headers"])
        assert res.status_code == 200, res.text
        assert res.json() == {"plan": "none", "active_until": None, "is_active": False}

        res = await client.post(
            f"{API}/buyer/subscription/checkout", headers=buyer_auth["headers"], json={"plan": "start"}
        )
        assert res.status_code == 200, res.text
        assert res.json()["is_active"] is True
        assert res.json()["plan"] == "start"

        sides = {
            row["side"]: row["plan"]
            for row in await fetch(
                "SELECT side, plan::text AS plan FROM supplier_subscriptions WHERE user_id = $1", user_id
            )
        }
        assert sides == {"buyer": "start"}

        res = await client.post(f"{API}/buyer/subscription/cancel", headers=buyer_auth["headers"])
        assert res.status_code == 200, res.text
        res = await client.get(f"{API}/buyer/subscription", headers=buyer_auth["headers"])
        assert res.json()["is_active"] is False


@pytest.mark.asyncio
async def test_paying_in_one_cabinet_does_not_cover_the_other(client):
    selling = await _login(client, "both@example.com", "Both123!", side="supplier")
    buying = await _login(client, "both@example.com", "Both123!", side="buyer")
    user_id = selling["me"]["user"]["id"]
    async with _plans_kept(user_id):
        res = await client.post(
            f"{API}/supplier/subscription/checkout", headers=selling["headers"], json={"plan": "pro"}
        )
        assert res.status_code == 200, res.text
        assert res.json()["is_active"] is True

        res = await client.get(f"{API}/supplier/subscription", headers=selling["headers"])
        assert res.json()["plan"] == "pro"
        res = await client.get(f"{API}/buyer/subscription", headers=buying["headers"])
        assert res.status_code == 200, res.text
        assert res.json()["is_active"] is False

        # The second cabinet is a second purchase.
        res = await client.post(
            f"{API}/buyer/subscription/checkout", headers=buying["headers"], json={"plan": "start"}
        )
        assert res.status_code == 200, res.text
        res = await client.get(f"{API}/buyer/subscription", headers=buying["headers"])
        assert res.json()["plan"] == "start"
        res = await client.get(f"{API}/supplier/subscription", headers=selling["headers"])
        assert res.json()["plan"] == "pro"
        assert await fetchval("SELECT COUNT(*) FROM supplier_subscriptions WHERE user_id = $1", user_id) == 2


@pytest.mark.asyncio
async def test_a_subscriber_request_stands_first_on_the_board(client, buyer_auth, supplier_auth):
    tag = f"Plan board {secrets.token_hex(4)}"
    _, rival_token, rival_me = await _register(client, "buyer")
    rival = _headers(rival_token, await _actor(client, rival_token, side="buyer"))
    subscriber_id = buyer_auth["me"]["user"]["id"]

    async def board() -> list[dict]:
        res = await client.get(
            f"{API}/supplier/rfqs/board", headers=supplier_auth["headers"], params={"q": tag}
        )
        assert res.status_code == 200, res.text
        return res.json()["items"]

    async with _plans_kept(subscriber_id, rival_me["user"]["id"]):
        older = await _publish(client, buyer_auth["headers"], f"{tag} older")
        newer = await _publish(client, rival, f"{tag} newer")

        # Without a plan the newest request is the first.
        items = await board()
        assert [item["id"] for item in items] == [newer, older]
        assert not any(item["promoted"] for item in items)

        # The plan of the buying cabinet lifts the request; the selling one's does not.
        await _set_plan(subscriber_id, "supplier", "business")
        assert [item["id"] for item in await board()] == [newer, older]
        await _set_plan(subscriber_id, "buyer", "start")
        items = await board()
        assert [item["id"] for item in items] == [older, newer]
        assert [item["promoted"] for item in items] == [True, False]
        assert items[0]["promotion_plan"] == "start"
        assert items[0]["buyer"]["subscription_plan"] == "start"

        # A higher plan stands above a lower one.
        await _set_plan(rival_me["user"]["id"], "buyer", "pro")
        assert [item["id"] for item in await board()] == [newer, older]

        public = await client.get(f"{API}/public/rfqs", params={"page_size": 100})
        assert public.status_code == 200, public.text
        listed = [item["id"] for item in public.json()["items"]]
        if newer in listed and older in listed:
            assert listed.index(newer) < listed.index(older)


@pytest.mark.asyncio
async def test_a_subscriber_offer_is_the_first_the_buyer_reads(client, buyer_auth, supplier_auth):
    rival = await _login(client, "both@example.com", "Both123!", side="supplier")
    first_id = supplier_auth["me"]["user"]["id"]
    rival_id = rival["me"]["user"]["id"]

    async def offers(rfq_id: str) -> list[dict]:
        res = await client.get(f"{API}/buyer/rfqs/{rfq_id}/proposals", headers=buyer_auth["headers"])
        assert res.status_code == 200, res.text
        return res.json()["items"]

    async with _plans_kept(first_id, rival_id):
        rfq_id = await _publish(client, buyer_auth["headers"], f"Plan offers {secrets.token_hex(4)}")
        res = await _submit(client, supplier_auth["headers"], rfq_id)
        assert res.status_code == 200, res.text
        early = res.json()["id"]
        res = await _submit(client, rival["headers"], rfq_id)
        assert res.status_code == 200, res.text
        late = res.json()["id"]

        items = await offers(rfq_id)
        assert [item["id"] for item in items] == [late, early]
        assert not any(item["promoted"] for item in items)

        await _set_plan(first_id, "supplier", "start")
        items = await offers(rfq_id)
        assert [item["id"] for item in items] == [early, late]
        assert [item["promoted"] for item in items] == [True, False]

        # The buying cabinet's plan says nothing about what the same person sells.
        await _set_plan(rival_id, "buyer", "business")
        assert [item["id"] for item in await offers(rfq_id)] == [early, late]
