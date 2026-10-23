import pytest

API = "/api/v1"


@pytest.mark.asyncio
async def test_buyer_support_ticket_flow(client, buyer_auth, admin_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")

    empty = await client.get(
        f"{API}/buyer/support/ticket",
        headers=buyer_auth["headers"],
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["ticket"] is None

    create = await client.post(
        f"{API}/buyer/support/ticket",
        headers=buyer_auth["headers"],
    )
    assert create.status_code == 200, create.text
    ticket = create.json()
    assert ticket["status"] == "open"
    assert ticket["side"] == "buyer"
    ticket_id = ticket["id"]

    again = await client.post(
        f"{API}/buyer/support/ticket",
        headers=buyer_auth["headers"],
    )
    assert again.status_code == 200
    assert again.json()["id"] == ticket_id

    send = await client.post(
        f"{API}/buyer/support/ticket/{ticket_id}/messages",
        headers=buyer_auth["headers"],
        data={"text": "Нужна помощь с договором"},
    )
    assert send.status_code == 200, send.text
    message = send.json()
    assert message["text"] == "Нужна помощь с договором"
    assert message["ticket_id"] == ticket_id
    assert message["attachment"] is None

    unread = await client.get(
        f"{API}/admin/support/unread-count",
        headers=admin_auth["headers"],
    )
    assert unread.status_code == 200, unread.text
    assert unread.json()["count"] >= 1

    admin_list = await client.get(
        f"{API}/admin/support/tickets",
        headers=admin_auth["headers"],
        params={"status": "open"},
    )
    assert admin_list.status_code == 200, admin_list.text
    assert any(item["id"] == ticket_id for item in admin_list.json()["items"])

    admin_reply = await client.post(
        f"{API}/admin/support/tickets/{ticket_id}/messages",
        headers=admin_auth["headers"],
        data={"text": "Здравствуйте, чем помочь?"},
    )
    assert admin_reply.status_code == 200, admin_reply.text
    reply_id = admin_reply.json()["id"]

    delivered = await client.post(
        f"{API}/buyer/support/ticket/{ticket_id}/messages/{reply_id}/delivered",
        headers=buyer_auth["headers"],
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] in {"delivered", "viewed"}

    read = await client.post(
        f"{API}/buyer/support/ticket/{ticket_id}/messages/read",
        headers=buyer_auth["headers"],
    )
    assert read.status_code == 200, read.text
    assert reply_id in read.json()["message_ids"]

    close = await client.post(
        f"{API}/buyer/support/ticket/{ticket_id}/close",
        headers=buyer_auth["headers"],
    )
    assert close.status_code == 200, close.text
    assert close.json()["status"] == "closed"


@pytest.mark.asyncio
async def test_supplier_support_create(client, supplier_auth):
    if not supplier_auth["actor_id"]:
        pytest.skip("No supplier actor")
    res = await client.post(
        f"{API}/supplier/support/ticket",
        headers=supplier_auth["headers"],
    )
    assert res.status_code == 200, res.text
    assert res.json()["side"] == "supplier"
