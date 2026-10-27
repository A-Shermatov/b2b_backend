import pytest

API = "/api/v1"


@pytest.mark.asyncio
async def test_direct_chat_get_or_create_and_message_roundtrip(
    client, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("Missing actor fixtures")

    first = await client.post(
        f"{API}/buyer/direct-chats/",
        headers=buyer_auth["headers"],
        json={"supplier_actor_id": supplier_auth["actor_id"]},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    chat_id = first_body["id"]
    assert first_body["supplier_actor_id"] == supplier_auth["actor_id"]
    assert first_body["buyer_actor_id"] == buyer_auth["actor_id"]

    second = await client.post(
        f"{API}/buyer/direct-chats/",
        headers=buyer_auth["headers"],
        json={"supplier_actor_id": supplier_auth["actor_id"]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == chat_id

    sent = await client.post(
        f"{API}/buyer/direct-chats/{chat_id}/messages",
        headers=buyer_auth["headers"],
        json={"text": "Здравствуйте"},
    )
    assert sent.status_code == 200, sent.text
    assert any(item["text"] == "Здравствуйте" for item in sent.json()["messages"])

    listed = await client.get(
        f"{API}/supplier/direct-chats/",
        headers=supplier_auth["headers"],
    )
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == chat_id for item in listed.json())

    thread = await client.get(
        f"{API}/supplier/direct-chats/{chat_id}",
        headers=supplier_auth["headers"],
    )
    assert thread.status_code == 200, thread.text
    assert thread.json()["unread_count"] >= 1
