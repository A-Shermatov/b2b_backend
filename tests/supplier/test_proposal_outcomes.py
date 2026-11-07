"""My proposals tell a supplier where each proposal stands, and who won."""

from datetime import date, timedelta

import pytest

from app.modules.proposals.services.read import proposal_outcome
from tests.conftest import _login
from tests.sql import fetchval

API = "/api/v1"

SUPPLIER_NAME_SQL = """
    SELECT COALESCE(NULLIF(c.title, ''), NULLIF(a.display_name, ''), 'Исполнитель')
    FROM actors a LEFT JOIN companies c ON c.id = a.company_id
    WHERE a.id = $1
"""


def test_outcome_rules():
    mine = {"id": 7, "status": "viewed"}
    open_rfq = {"status": "receiving_proposals"}
    assert proposal_outcome(mine, open_rfq, None) == {"kind": "waiting"}
    # Past the deadline the buyer can still choose: still waiting.
    assert proposal_outcome(mine, {"status": "expired"}, None) == {"kind": "waiting"}
    assert proposal_outcome(mine, open_rfq, {"proposal_id": 7, "contract_id": 3, "name": "Я"}) == {
        "kind": "won",
        "contract_id": 3,
    }
    assert proposal_outcome(mine, open_rfq, {"proposal_id": 9, "contract_id": 4, "name": "Другой"}) == {
        "kind": "lost",
        "winner_name": "Другой",
    }
    # A winner tells more than a refusal: the supplier learns who got it.
    rejected = {"id": 7, "status": "rejected"}
    assert proposal_outcome(rejected, open_rfq, {"proposal_id": 9, "contract_id": 4, "name": "Другой"})[
        "kind"
    ] == "lost"
    assert proposal_outcome(rejected, open_rfq, None) == {"kind": "rejected"}
    assert proposal_outcome({"id": 7, "status": "withdrawn"}, open_rfq, None) == {"kind": "withdrawn"}
    assert proposal_outcome({"id": 7, "status": "pending_supplier"}, open_rfq, None) == {"kind": "your_move"}
    assert proposal_outcome(mine, {"status": "cancelled"}, None) == {"kind": "closed"}
    assert proposal_outcome({"id": 7, "status": "archived"}, open_rfq, None) == {"kind": "closed"}


async def _published_rfq(client, buyer_auth, title: str) -> str:
    res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": title,
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": (date.today() + timedelta(days=30)).isoformat(),
            "visibility": "public",
            "status": "draft",
            "project_duration": "2 weeks",
            "start_date": (date.today() + timedelta(days=35)).isoformat(),
        },
    )
    assert res.status_code == 200, res.text
    rfq_id = res.json()["id"]
    published = await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_auth["headers"])
    assert published.status_code == 200, published.text
    return rfq_id


async def _propose(client, auth, rfq_id: str, price: int) -> int:
    res = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=auth["headers"],
        json={"price": price, "currency": "TJS", "delivery_time": "10 days", "message": "Готовы"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def _my_proposal(client, auth, proposal_id: int) -> dict:
    res = await client.get(
        f"{API}/supplier/proposals/", headers=auth["headers"], params={"page_size": 100}
    )
    assert res.status_code == 200, res.text
    return next(item for item in res.json()["items"] if item["id"] == proposal_id)


@pytest.mark.asyncio
async def test_suppliers_see_who_won_the_request(client, buyer_auth, supplier_auth):
    rival_auth = await _login(client, "both@example.com", "Both123!", side="supplier")
    if not (buyer_auth["actor_id"] and supplier_auth["actor_id"] and rival_auth["actor_id"]):
        pytest.skip("Missing actor fixtures")

    title = "Кто выиграл заявку"
    rfq_id = await _published_rfq(client, buyer_auth, title)
    winner_id = await _propose(client, supplier_auth, rfq_id, 12000)
    rival_id = await _propose(client, rival_auth, rfq_id, 11000)

    # Before the choice both wait, and see which request it is.
    for auth, proposal_id in ((supplier_auth, winner_id), (rival_auth, rival_id)):
        item = await _my_proposal(client, auth, proposal_id)
        assert item["outcome"] == {"kind": "waiting"}
        assert item["rfq_summary"]["title"] == title
        assert item["rfq_summary"]["deadline"]

    accepted = await client.post(
        f"{API}/buyer/proposals/{winner_id}/accept",
        headers=buyer_auth["headers"],
        json={"payment_type": "full_prepayment"},
    )
    assert accepted.status_code == 200, accepted.text
    contract_id = accepted.json()["contract_id"]

    won = await _my_proposal(client, supplier_auth, winner_id)
    assert won["status"] == "accepted"
    assert won["outcome"] == {"kind": "won", "contract_id": contract_id}

    # The other proposal is archived by the choice, yet stays in the list with the winner's name.
    lost = await _my_proposal(client, rival_auth, rival_id)
    assert lost["status"] == "archived"
    winner_name = await fetchval(SUPPLIER_NAME_SQL, supplier_auth["actor_id"])
    assert lost["outcome"] == {"kind": "lost", "winner_name": winner_name}
    assert lost["rfq_summary"]["title"] == title
