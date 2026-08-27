import pytest

from tests.conftest import _login

API = "/api/v1"


def _company_supplier_actor_id(auth: dict) -> int | None:
    actor = next(
        (
            a
            for a in auth["me"]["actors"]
            if a.get("side") == "supplier" and a.get("kind") == "company"
        ),
        None,
    )
    return actor["id"] if actor else None


async def _create_and_publish_public_rfq(client, buyer_auth, title: str) -> str:
    create_res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": title,
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": "2026-12-31",
            "visibility": "public",
            "status": "draft",
            "project_duration": "2 months",
            "start_date": "2026-07-01",
        },
    )
    assert create_res.status_code == 200, create_res.text
    rfq_id = create_res.json()["id"]

    publish_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    assert publish_res.status_code == 200, publish_res.text
    return rfq_id


@pytest.mark.asyncio
async def test_invite_makes_rfq_exclusive_on_supplier_board(
    client, buyer_auth, supplier_auth
):
    invited_actor_id = _company_supplier_actor_id(supplier_auth)
    if not buyer_auth["actor_id"] or not invited_actor_id:
        pytest.skip("Missing buyer or company supplier actor")

    other_supplier = await _login(
        client, "both@example.com", "Both123!", side="supplier"
    )
    other_actor_id = _company_supplier_actor_id(other_supplier)
    if not other_actor_id:
        pytest.skip("No second company supplier actor (both@example.com)")

    invited_headers = {
        **supplier_auth["headers"],
        "X-Actor-Id": str(invited_actor_id),
    }
    other_headers = {
        **other_supplier["headers"],
        "X-Actor-Id": str(other_actor_id),
    }

    rfq_id = await _create_and_publish_public_rfq(
        client, buyer_auth, "Invite exclusive RFQ"
    )

    before_invited = await client.get(
        f"{API}/supplier/rfqs/board",
        headers=invited_headers,
    )
    assert before_invited.status_code == 200, before_invited.text
    assert any(item["id"] == rfq_id for item in before_invited.json())

    before_other = await client.get(
        f"{API}/supplier/rfqs/board",
        headers=other_headers,
    )
    assert before_other.status_code == 200, before_other.text
    assert any(item["id"] == rfq_id for item in before_other.json())

    invite_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/invite",
        headers=buyer_auth["headers"],
        json={"supplier_ids": [invited_actor_id]},
    )
    assert invite_res.status_code == 200, invite_res.text
    invited_rfq = invite_res.json()
    assert invited_rfq["visibility"] == "invited_only"
    assert invited_actor_id in invited_rfq["invited_supplier_ids"]

    after_invited = await client.get(
        f"{API}/supplier/rfqs/board",
        headers=invited_headers,
    )
    assert after_invited.status_code == 200, after_invited.text
    assert any(item["id"] == rfq_id for item in after_invited.json())

    after_other = await client.get(
        f"{API}/supplier/rfqs/board",
        headers=other_headers,
    )
    assert after_other.status_code == 200, after_other.text
    assert all(item["id"] != rfq_id for item in after_other.json())

    public_board = await client.get(f"{API}/public/rfqs")
    assert public_board.status_code == 200, public_board.text
    assert all(item["id"] != rfq_id for item in public_board.json())
