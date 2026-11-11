"""The bot in private chats: linking an account, the feed with offers, a request from a message.

Telegram itself is faked: `telegram.call_bot` records what the bot would send.
"""

import asyncio
import json
import secrets

import pytest

from app.modules.telegram import assistant, links, widgets
from app.modules.telegram import service as telegram
from tests.buyer.test_deadlines_selfdeal import _rfq_payload
from tests.sql import execute, fetchrow, fetchval

API = "/api/v1"
TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-bs"
SITE = "https://brandmarket.tj"


class Calls(list):
    """What the bot sent, as (method, payload) pairs, plus the updates waiting for getUpdates."""

    def __init__(self):
        super().__init__()
        self.updates: list[dict] = []


@pytest.fixture
def bot(monkeypatch):
    calls = Calls()
    updates = calls.updates

    async def fake_call(token, method, payload=None, files=None, timeout=None):
        assert token == TOKEN
        calls.append((method, dict(payload or {})))
        if method == "getMe":
            return {"username": "brandmarket_bot"}
        if method == "getUpdates":
            batch, updates[:] = list(updates), []
            return batch
        return {"message_id": len(calls)}

    monkeypatch.setattr(telegram, "call_bot", fake_call)
    return calls


@pytest.fixture
async def assistant_on(client, admin_auth, bot):
    """The admin has saved the token and turned the assistant on; cleaned up after."""
    previous = await fetchrow("SELECT * FROM telegram_settings WHERE id = 1")
    await execute("DELETE FROM telegram_settings")
    res = await client.put(
        f"{API}/admin/telegram",
        headers=admin_auth["headers"],
        json={"enabled": False, "assistant_enabled": True, "bot_token": TOKEN, "site_url": SITE},
    )
    assert res.status_code == 200, res.text
    assert res.json()["assistant_enabled"] is True
    assert TOKEN not in res.text
    # Turning the assistant on gives the bot its menu in private chats.
    menu = [payload for method, payload in bot if method == "setMyCommands"]
    assert [c["command"] for c in menu[-1]["commands"]] == ["zayavki", "help"]
    yield await links.assistant_state()
    await asyncio.gather(*list(assistant._background), return_exceptions=True)
    await execute("DELETE FROM telegram_accounts")
    await execute("DELETE FROM telegram_link_codes")
    await execute("DELETE FROM telegram_drafts")
    await execute("DELETE FROM telegram_settings")
    if previous:
        await execute(
            """
            INSERT INTO telegram_settings (id, enabled, bot_token_enc, bot_username, channel_id,
                                           channel_title, site_url, updated_at)
            VALUES (1, $1, $2, $3, $4, $5, $6, NOW())
            """,
            previous["enabled"],
            previous["bot_token_enc"],
            previous["bot_username"],
            previous["channel_id"],
            previous["channel_title"],
            previous["site_url"],
        )


def _message(telegram_id: int, text: str) -> dict:
    return {
        "update_id": secrets.randbelow(10**9),
        "message": {
            "message_id": 1,
            "from": {"id": telegram_id, "is_bot": False, "first_name": "Тест", "username": f"user{telegram_id}"},
            "chat": {"id": telegram_id, "type": "private"},
            "text": text,
        },
    }


async def _link(client, auth, state, telegram_id: int) -> None:
    res = await client.post(f"{API}/telegram/account/link", headers=auth["headers"])
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert url.startswith("https://t.me/brandmarket_bot?start=")
    await assistant.handle_update(state, _message(telegram_id, f"/start {url.split('start=')[1]}"))


def test_widgets_fit_what_telegram_accepts():
    for widget in (widgets.FEED_WIDGET, widgets.REQUEST_WIDGET):
        raw, packed = widgets.sizes(widget)
        assert raw <= widgets.HTML_LIMIT
        # Half of the limit is kept free, so a small change cannot push a widget over it.
        assert packed <= widgets.PACKED_LIMIT * 0.75
        # No text inputs and nothing from the network: the sandbox drops both.
        assert "<input" not in widget and "fetch(" not in widget and "src=" not in widget
    long_names = [["category-slug-number-%d" % i, "Очень длинное название категории товаров и услуг"] for i in range(12)]
    data = assistant._draft_data("0123456789", "Ж" * assistant.TITLE_LENGTH, long_names)
    assert len(data.encode("utf-8")) <= widgets.DATA_LIMIT
    assert json.loads(data)["g"] == assistant.CITIES


@pytest.mark.asyncio
async def test_the_card_is_hidden_until_the_admin_turns_the_bot_on(client, buyer_auth, bot):
    await execute("DELETE FROM telegram_settings")
    res = await client.get(f"{API}/telegram/account", headers=buyer_auth["headers"])
    assert res.status_code == 200, res.text
    assert res.json()["available"] is False
    res = await client.post(f"{API}/telegram/account/link", headers=buyer_auth["headers"])
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_a_person_links_their_account_with_a_one_time_code(client, buyer_auth, assistant_on, bot):
    state = assistant_on
    res = await client.get(f"{API}/telegram/account", headers=buyer_auth["headers"])
    assert res.json() == {
        "available": True,
        "bot_username": "brandmarket_bot",
        "linked": False,
        "telegram_username": None,
        "telegram_name": None,
        "linked_at": None,
    }

    # Someone who never linked is told how, not served.
    await assistant.handle_update(state, _message(700, "Нужны ноутбуки"))
    assert "подключите аккаунт" in bot[-1][1]["text"]

    await assistant.handle_update(state, _message(700, "/start not-a-real-code"))
    assert "устарела" in bot[-1][1]["text"]

    await _link(client, buyer_auth, state, 700)
    assert "подключён" in bot[-1][1]["text"]
    view = (await client.get(f"{API}/telegram/account", headers=buyer_auth["headers"])).json()
    assert view["linked"] is True and view["telegram_username"] == "user700"

    # A code works once.
    used = await fetchval("SELECT code FROM telegram_link_codes WHERE used_at IS NOT NULL LIMIT 1")
    await assistant.handle_update(state, _message(701, f"/start {used}"))
    assert "устарела" in bot[-1][1]["text"]
    assert await links.user_of_telegram(701) is None

    res = await client.delete(f"{API}/telegram/account", headers=buyer_auth["headers"])
    assert res.json()["linked"] is False
    assert await links.user_of_telegram(700) is None


@pytest.mark.asyncio
async def test_a_buyer_posts_a_request_by_writing_to_the_bot(client, buyer_auth, assistant_on, bot):
    state = assistant_on
    user_id = buyer_auth["me"]["user"]["id"]
    await _link(client, buyer_auth, state, 710)
    title = f"Нужен сайт-каталог {secrets.token_hex(3)}"

    await assistant.handle_update(state, _message(710, f"{title}\nНа 30 товаров, с формой заявки."))
    method, sent = bot[-1]
    assert method == "sendWidget"
    assert sent["html"] == widgets.REQUEST_WIDGET and sent["allow_queries"] is True
    data = json.loads(sent["chat_data"])
    assert data["t"] == title
    assert ["it", await fetchval("SELECT name FROM categories WHERE slug = 'it'")] in data["c"]

    # The widget's "Опубликовать": a service, category "it", offers for 7 days.
    answer = await assistant.answer_query(user_id, f"p:{data['id']}:s:it:7:0")
    assert answer["ok"] == 1, answer
    rfq = await fetchrow("SELECT title, description, status::text AS status, type::text AS type, deadline FROM rfqs WHERE id = $1", answer["id"])
    assert rfq["title"] == title
    assert "На 30 товаров" in rfq["description"]
    assert rfq["type"] == "service"
    assert rfq["status"] in ("published", "receiving_proposals")
    assert len(rfq["deadline"]) == 10

    # A second tap publishes nothing new.
    again = await assistant.answer_query(user_id, f"p:{data['id']}:s:it:7:0")
    assert again["id"] == answer["id"]
    assert (await assistant.answer_query(user_id, "p:nope:s:it:7:0"))["e"]
    assert (await assistant.answer_query(user_id, f"p:{data['id']}:s:it:5:0"))["id"] == answer["id"]

    # Goods take a city; an unknown category is refused.
    await assistant.handle_update(state, _message(710, "Нужно 25 ноутбуков для офиса"))
    draft = json.loads(bot[-1][1]["chat_data"])
    assert (await assistant.answer_query(user_id, f"p:{draft['id']}:p:no-such-category:7:1"))["e"]
    goods = await assistant.answer_query(user_id, f"p:{draft['id']}:p:it:14:1")
    assert goods["ok"] == 1, goods
    city = await fetchval("SELECT delivery_city FROM rfqs WHERE id = $1", goods["id"])
    assert city == assistant.CITIES[1]


@pytest.mark.asyncio
async def test_a_supplier_reads_the_feed_and_answers_in_the_chat(
    client, buyer_auth, supplier_auth, assistant_on, bot
):
    state = assistant_on
    supplier_id = supplier_auth["me"]["user"]["id"]
    await _link(client, supplier_auth, state, 720)

    res = await client.post(
        f"{API}/buyer/rfqs/", headers=buyer_auth["headers"], json=_rfq_payload(f"Feed in Telegram {secrets.token_hex(3)}")
    )
    rfq_id = res.json()["id"]
    assert (await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_auth["headers"])).status_code == 200

    await assistant.handle_update(state, _message(720, "/zayavki"))
    method, sent = bot[-1]
    assert method == "sendWidget" and sent["html"] == widgets.FEED_WIDGET
    first = json.loads(sent["chat_data"])
    assert first["n"] >= 1 and first["i"] == 0
    assert len(sent["chat_data"].encode("utf-8")) <= widgets.DATA_LIMIT

    # Leafing through: every place of the feed answers, the last wraps to a real one.
    seen = {}
    for index in range(min(first["n"], 40)):
        item = await assistant.answer_query(supplier_id, f"f:{index}")
        assert item["i"] == index and item["n"] >= 1
        seen[item["id"]] = item
    assert rfq_id in seen
    assert seen[rfq_id]["m"] == 0 and seen[rfq_id]["k"] == "услуга"
    beyond = await assistant.answer_query(supplier_id, "f:99999")
    assert beyond["i"] == 0

    # The offer from the widget's keypad: 1500 for 10 days.
    assert (await assistant.answer_query(supplier_id, f"o:{rfq_id}:0:10"))["e"]
    assert (await assistant.answer_query(supplier_id, f"o:{rfq_id}:1500:10")) == {"ok": 1}
    offer = await fetchrow(
        "SELECT price, delivery_time, status::text AS status FROM proposals WHERE rfq_id = $1", rfq_id
    )
    assert float(offer["price"]) == 1500 and offer["delivery_time"] == "10"
    # Answering twice is refused in words, and the feed now says "answered".
    assert (await assistant.answer_query(supplier_id, f"o:{rfq_id}:1400:9"))["e"]
    listed = [await assistant.answer_query(supplier_id, f"f:{i}") for i in range(min(first["n"], 40))]
    assert next(item for item in listed if item["id"] == rfq_id)["m"] == 1

    # The buyer's own request is not theirs to answer; an unlinked person gets words, not data.
    assert (await assistant.answer_query(buyer_auth["me"]["user"]["id"], f"o:{rfq_id}:900:5"))["e"]
    assert (await assistant.answer_query(None, "f:0"))["e"]
    assert (await assistant.answer_query(supplier_id, "what"))["e"]


@pytest.mark.asyncio
async def test_widget_queries_are_answered_and_updates_are_read_in_order(
    client, supplier_auth, assistant_on, bot
):
    state = assistant_on
    await _link(client, supplier_auth, state, 730)
    bot.updates.extend(
        [
            {"update_id": 41, "widget_query": {"id": "q-1", "from": {"id": 730}, "data": "f:0"}},
            {"update_id": 42, "widget_query": {"id": "q-2", "from": {"id": 999}, "data": "f:0"}},
        ]
    )
    assert await assistant.poll_once(state) == 2
    answers = [payload for method, payload in bot if method == "answerWidgetQuery"]
    assert [a["widget_query_id"] for a in answers] == ["q-1", "q-2"]
    assert "n" in json.loads(answers[0]["data"])
    assert "e" in json.loads(answers[1]["data"])
    assert all(len(a["data"].encode("utf-8")) <= widgets.DATA_LIMIT for a in answers)
    # The next read starts after the last update, and that is kept in the database.
    assert state["update_offset"] == 43
    assert await fetchval("SELECT update_offset FROM telegram_settings WHERE id = 1") == 43
    polls = [payload for method, payload in bot if method == "getUpdates"]
    assert polls[-1]["allowed_updates"] == ["message", "widget_query"]


@pytest.mark.asyncio
async def test_notifications_are_repeated_to_the_linked_chat(client, buyer_auth, assistant_on, bot):
    state = assistant_on
    user_id = buyer_auth["me"]["user"]["id"]
    await assistant.mirror_notification(user_id, "Новое предложение", "Текст", "/customer/rfqs/1")
    assert not [1 for method, _ in bot if method == "sendMessage"]

    await _link(client, buyer_auth, state, 740)
    await assistant.mirror_notification(user_id, "Новое <предложение>", "Цена 1 500 TJS", "/customer/rfqs/1")
    method, sent = bot[-1]
    assert method == "sendMessage" and sent["chat_id"] == 740
    assert "Новое &lt;предложение&gt;" in sent["text"]
    assert sent["reply_markup"]["inline_keyboard"][0][0]["url"] == f"{SITE}/customer/rfqs/1"


@pytest.mark.asyncio
async def test_steps_taken_in_the_bot_are_kept_in_the_journal(
    client, admin_auth, supplier_auth, assistant_on, bot
):
    state = assistant_on
    # The journal is never cleaned between runs: a chat of its own each time.
    chat = 1_000_000 + secrets.randbelow(10**9)
    await _link(client, supplier_auth, state, chat)
    await assistant.handle_update(state, _message(chat, "/zayavki"))
    await assistant.handle_update(
        state, {"update_id": 51, "widget_query": {"id": "q-9", "from": {"id": chat}, "data": "f:0"}}
    )
    # A stranger's taps are answered, but there is nobody to write them down for.
    await assistant.handle_update(
        state, {"update_id": 52, "widget_query": {"id": "q-10", "from": {"id": 998}, "data": "f:0"}}
    )

    res = await client.get(
        f"{API}/admin/logs",
        params={"source": "events", "category": "telegram", "device": f"tg-{chat}"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [item["action"] for item in items] == ["telegram.feed.view", "telegram.command"]
    assert items[1]["details"] == {"command": "/zayavki"}
    assert items[0]["details"]["request"] == "f:0"
    assert all(item["user"]["email"] == "supplier@example.com" for item in items)
    # The chat is the device these steps came from.
    assert items[0]["device"]["browser"] == "Telegram"
    assert items[0]["device"]["type"] == "messenger"
    assert not await fetchval("SELECT COUNT(*) FROM audit_logs WHERE device_key = 'tg-998'")

    card = await client.get(f"{API}/admin/devices/tg-{chat}", headers=admin_auth["headers"])
    assert card.status_code == 200, card.text
    assert [user["email"] for user in card.json()["users"]] == ["supplier@example.com"]
