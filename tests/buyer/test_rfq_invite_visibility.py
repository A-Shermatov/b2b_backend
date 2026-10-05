import pytest

from tests.conftest import _login

API = "/api/v1"


def _supplier_actor_id(auth: dict, kind: str) -> int | None:
    actor = next(
        (
            a
            for a in auth["me"]["actors"]
            if a.get("side") == "supplier" and a.get("kind") == kind
        ),
        None,
    )
    return actor["id"] if actor else None


def _company_supplier_actor_id(auth: dict) -> int | None:
    return _supplier_actor_id(auth, "company")


def _service_rfq_payload(title: str, **overrides) -> dict:
    return {
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
        **overrides,
    }


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


@pytest.mark.asyncio
async def test_create_personal_rfq_for_individual_supplier(
    client, buyer_auth, supplier_auth
):
    invited_actor_id = _supplier_actor_id(supplier_auth, "individual")
    if not buyer_auth["actor_id"] or not invited_actor_id:
        pytest.skip("Missing buyer or individual supplier actor")

    other_supplier = await _login(
        client, "both@example.com", "Both123!", side="supplier"
    )
    other_actor_id = other_supplier["actor_id"]
    if not other_actor_id or other_actor_id == invited_actor_id:
        pytest.skip("No second supplier actor (both@example.com)")

    create_res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json=_service_rfq_payload(
            "Personal RFQ for individual",
            visibility="invited_only",
            invited_supplier_ids=[invited_actor_id],
        ),
    )
    assert create_res.status_code == 200, create_res.text
    created = create_res.json()
    assert created["visibility"] == "invited_only"
    assert created["invited_supplier_ids"] == [invited_actor_id]
    rfq_id = created["id"]

    publish_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/publish",
        headers=buyer_auth["headers"],
    )
    assert publish_res.status_code == 200, publish_res.text

    invited_headers = {
        **supplier_auth["headers"],
        "X-Actor-Id": str(invited_actor_id),
    }
    board = await client.get(f"{API}/supplier/rfqs/board", headers=invited_headers)
    assert board.status_code == 200, board.text
    item = next((r for r in board.json() if r["id"] == rfq_id), None)
    assert item is not None
    assert item["is_invited"] is True
    assert item["invited_supplier_ids"] == []

    detail = await client.get(f"{API}/supplier/rfqs/{rfq_id}", headers=invited_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_invited"] is True

    other_board = await client.get(
        f"{API}/supplier/rfqs/board", headers=other_supplier["headers"]
    )
    assert other_board.status_code == 200, other_board.text
    assert all(r["id"] != rfq_id for r in other_board.json())

    other_detail = await client.get(
        f"{API}/supplier/rfqs/{rfq_id}", headers=other_supplier["headers"]
    )
    assert other_detail.status_code == 403, other_detail.text

    public_board = await client.get(f"{API}/public/rfqs")
    assert public_board.status_code == 200, public_board.text
    assert all(r["id"] != rfq_id for r in public_board.json())


@pytest.mark.asyncio
async def test_personal_rfq_without_valid_supplier_is_rejected(client, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("Missing buyer actor")

    no_ids = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json=_service_rfq_payload("Personal RFQ without supplier", visibility="invited_only"),
    )
    assert no_ids.status_code == 422, no_ids.text

    buyer_as_supplier = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json=_service_rfq_payload(
            "Personal RFQ for buyer actor",
            visibility="invited_only",
            invited_supplier_ids=[buyer_auth["actor_id"]],
        ),
    )
    assert buyer_as_supplier.status_code == 422, buyer_as_supplier.text

    rfq_id = await _create_and_publish_public_rfq(
        client, buyer_auth, "Invite invalid supplier RFQ"
    )
    invite_res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/invite",
        headers=buyer_auth["headers"],
        json={"supplier_ids": [buyer_auth["actor_id"]]},
    )
    assert invite_res.status_code == 422, invite_res.text
