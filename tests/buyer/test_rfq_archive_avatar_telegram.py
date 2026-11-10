"""Cancelling and archiving requests, profile photos in chats, and the Telegram channel bot."""

import asyncio
import io
import re
from datetime import date, timedelta

import pytest
from PIL import Image

from app.modules.telegram import card as telegram_card
from app.modules.telegram import service as telegram
from tests.sql import execute, fetchrow, fetchval

API = "/api/v1"
TOKEN = "123456789:AAEhBP0av18kHnoOKoH0cKjXyZ0123456789a"
# The smallest valid PNG: one transparent pixel.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f0300050001ff0a2db4b80000000049454e44ae426082"
)


async def _request(client, buyer_auth, title: str, *, publish: bool = True, visibility="public") -> str:
    created = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "product",
            "title": title,
            "category_id": "it",
            "budget_type": "fixed",
            "budget_from": 12500,
            "currency": "TJS",
            "deadline": (date.today() + timedelta(days=20)).isoformat(),
            "visibility": visibility,
            "status": "draft",
            "quantity": 40,
            "delivery_country": "Таджикистан",
            "delivery_city": "Душанбе",
            "delivery_date": (date.today() + timedelta(days=25)).isoformat(),
        },
    )
    assert created.status_code == 200, created.text
    rfq_id = created.json()["id"]
    if publish:
        res = await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_auth["headers"])
        assert res.status_code == 200, res.text
    return rfq_id


@pytest.mark.asyncio
async def test_buyer_cancels_with_a_reason_and_the_supplier_is_told(client, buyer_auth, supplier_auth):
    rfq_id = await _request(client, buyer_auth, "Отмена заявки с причиной")
    proposal = await client.post(
        f"{API}/supplier/rfqs/{rfq_id}/proposals",
        headers=supplier_auth["headers"],
        json={"price": 12000, "currency": "TJS", "delivery_time": "5 дней", "message": "Готовы"},
    )
    assert proposal.status_code == 200, proposal.text

    res = await client.post(
        f"{API}/buyer/rfqs/{rfq_id}/close",
        headers=buyer_auth["headers"],
        json={"reason": "Нашли поставщика сами"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "cancelled"
    assert body["cancel_reason"] == "Нашли поставщика сами"
    told = await fetchval(
        "SELECT COUNT(*) FROM notifications WHERE title = 'Заявка отменена' AND body LIKE $1",
        "%Нашли поставщика сами%",
    )
    assert told >= 1


@pytest.mark.asyncio
async def test_archive_hides_a_closed_request_and_restores_it(client, buyer_auth):
    rfq_id = await _request(client, buyer_auth, "Заявка для архива")

    early = await client.post(f"{API}/buyer/rfqs/{rfq_id}/archive", headers=buyer_auth["headers"])
    assert early.status_code == 409, early.text

    await client.post(f"{API}/buyer/rfqs/{rfq_id}/close", headers=buyer_auth["headers"])
    archived = await client.post(f"{API}/buyer/rfqs/{rfq_id}/archive", headers=buyer_auth["headers"])
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None
    assert archived.json()["status"] == "cancelled"

    async def ids(tab: str) -> set[str]:
        res = await client.get(
            f"{API}/buyer/rfqs/?tab={tab}&page_size=100", headers=buyer_auth["headers"]
        )
        assert res.status_code == 200, res.text
        return {item["id"] for item in res.json()["items"]}

    assert rfq_id in await ids("archived")
    assert rfq_id not in await ids("all")
    assert rfq_id not in await ids("closed")

    restored = await client.post(f"{API}/buyer/rfqs/{rfq_id}/unarchive", headers=buyer_auth["headers"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None
    assert rfq_id in await ids("closed")
    assert rfq_id not in await ids("archived")


@pytest.mark.asyncio
async def test_profile_photo_is_saved_and_shown_next_to_messages(client, buyer_auth, supplier_auth):
    not_image = await client.post(
        f"{API}/auth/me/avatar",
        headers=buyer_auth["headers"],
        files={"file": ("note.pdf", b"%PDF-1.4\n1 0 obj\n", "application/pdf")},
    )
    assert not_image.status_code == 422, not_image.text

    res = await client.post(
        f"{API}/auth/me/avatar",
        headers=buyer_auth["headers"],
        files={"file": ("me.png", PNG, "image/png")},
    )
    assert res.status_code == 200, res.text
    avatar = res.json()["avatar_url"]
    assert avatar and avatar.endswith(".png")

    me = await client.get(f"{API}/auth/me", headers=buyer_auth["headers"])
    assert me.json()["user"]["avatar_url"] == avatar

    chat = await client.post(
        f"{API}/buyer/direct-chats/",
        headers=buyer_auth["headers"],
        json={"supplier_actor_id": supplier_auth["actor_id"]},
    )
    assert chat.status_code == 200, chat.text
    sent = await client.post(
        f"{API}/buyer/direct-chats/{chat.json()['id']}/messages",
        headers=buyer_auth["headers"],
        json={"text": "Здравствуйте, это я с фото"},
    )
    assert sent.status_code == 200, sent.text
    mine = [m for m in sent.json()["messages"] if m["text"] == "Здравствуйте, это я с фото"]
    assert mine and mine[-1]["sender_avatar"] == avatar

    # The supplier sees the buyer's photo on the thread too.
    threads = await client.get(f"{API}/supplier/direct-chats/", headers=supplier_auth["headers"])
    thread = next(t for t in threads.json() if t["id"] == chat.json()["id"])
    assert thread["last_message"]["sender_avatar"] == avatar

    removed = await client.delete(f"{API}/auth/me/avatar", headers=buyer_auth["headers"])
    assert removed.status_code == 200
    assert removed.json()["avatar_url"] is None


@pytest.fixture
def telegram_calls(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_call(token, method, payload=None, files=None):
        assert token == TOKEN
        calls.append((method, {**(payload or {}), **({"_files": files} if files else {})}))
        if method == "getMe":
            return {"username": "brendmarket_bot"}
        if method == "getChat":
            return {"title": "Заявки Brend Market"}
        return {"message_id": len(calls)}

    monkeypatch.setattr(telegram, "call_bot", fake_call)
    return calls


@pytest.mark.asyncio
async def test_admin_connects_the_bot_and_new_requests_are_posted(
    client, admin_auth, buyer_auth, telegram_calls
):
    await execute("DELETE FROM telegram_settings")

    bad = await client.put(
        f"{API}/admin/telegram",
        headers=admin_auth["headers"],
        json={"enabled": True, "bot_token": "not-a-token", "channel_id": "@brend_zayavki"},
    )
    assert bad.status_code == 422, bad.text

    saved = await client.put(
        f"{API}/admin/telegram",
        headers=admin_auth["headers"],
        json={
            "enabled": True,
            "bot_token": TOKEN,
            "channel_id": "@brend_zayavki",
            "site_url": "https://brendmarket.tj",
        },
    )
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert view["enabled"] is True
    assert view["bot_username"] == "brendmarket_bot"
    assert view["channel_title"] == "Заявки Brend Market"
    # The token never comes back to the browser.
    assert TOKEN not in saved.text
    assert view["token_hint"].endswith("789a")
    stored = await fetchval("SELECT bot_token_enc FROM telegram_settings WHERE id = 1")
    assert TOKEN not in stored

    rfq_id = await _request(client, buyer_auth, "Ноутбуки для отдела продаж")
    await asyncio.gather(*list(telegram._background))
    posts = [(m, p) for m, p in telegram_calls if m in ("sendPhoto", "sendMessage")]
    assert len(posts) == 1
    method, sent = posts[0]
    # The post is a picture card with a caption, or text alone where no font is installed.
    if telegram_card.fonts_available():
        assert method == "sendPhoto"
        _name, picture, mime = sent["_files"]["photo"]
        assert mime == "image/png" and picture[1:4] == b"PNG"
    text = sent.get("caption") or sent["text"]
    assert text.startswith("🆕 <b>Ноутбуки для отдела продаж</b>")
    assert "🏢 Заказчик: <b>" in text
    assert "📦 Количество: 40 шт" in text
    assert "💰 Бюджет: 12" in text and "500 TJS" in text
    assert "📍 Город: Душанбе" in text
    # Both dates in bold, as day.month.year and time to the minute.
    assert re.search(r"⏳ <b>Приём КП до \d\d\.\d\d\.\d{4}, 23:59</b>", text)
    assert re.search(r"📅 <b>Дедлайн: до \d\d\.\d\d\.\d{4}, 23:59</b>", text)
    # No link line in the text: the request opens with the button under the post.
    assert "Откликнуться" not in text and "<a " not in text
    assert sent["reply_markup"]["inline_keyboard"][0][0]["url"] == (
        f"https://brendmarket.tj/supplier/rfqs/{rfq_id}"
    )
    post = await fetchrow("SELECT status FROM telegram_posts WHERE rfq_id = $1", rfq_id)
    assert post["status"] == "sent"

    # Publishing again or posting twice never duplicates the post.
    assert await telegram.post_request(rfq_id) == "duplicate"

    # A test message and the log are available to the admin.
    test = await client.post(f"{API}/admin/telegram/test", headers=admin_auth["headers"])
    assert test.status_code == 200, test.text
    logged = await client.get(f"{API}/admin/telegram", headers=admin_auth["headers"])
    assert any(item["rfq_id"] == rfq_id for item in logged.json()["recent"])


@pytest.mark.asyncio
async def test_private_requests_and_a_switched_off_bot_post_nothing(
    client, admin_auth, buyer_auth, supplier_auth, telegram_calls
):
    await execute("DELETE FROM telegram_settings")
    await client.put(
        f"{API}/admin/telegram",
        headers=admin_auth["headers"],
        json={"enabled": True, "bot_token": TOKEN, "channel_id": "-1001234567890"},
    )
    created = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={
            "type": "service",
            "title": "Персональная заявка исполнителю",
            "category_id": "it",
            "budget_type": "open",
            "currency": "TJS",
            "deadline": (date.today() + timedelta(days=20)).isoformat(),
            "visibility": "invited_only",
            "invited_supplier_ids": [supplier_auth["actor_id"]],
            "status": "draft",
            "project_duration": "1 месяц",
            "start_date": (date.today() + timedelta(days=25)).isoformat(),
        },
    )
    assert created.status_code == 200, created.text
    private_id = created.json()["id"]
    await client.post(f"{API}/buyer/rfqs/{private_id}/publish", headers=buyer_auth["headers"])
    assert await telegram.post_request(private_id) == "skipped"

    await client.put(
        f"{API}/admin/telegram",
        headers=admin_auth["headers"],
        json={"enabled": False, "channel_id": "-1001234567890"},
    )
    public_id = await _request(client, buyer_auth, "Заявка при выключенном боте")
    await asyncio.gather(*list(telegram._background))
    assert await telegram.post_request(public_id) == "skipped"
    assert not [p for m, p in telegram_calls if m == "sendMessage"]


@pytest.mark.asyncio
async def test_a_refused_picture_still_posts_the_text(
    client, admin_auth, buyer_auth, telegram_calls, monkeypatch
):
    await execute("DELETE FROM telegram_settings")
    await client.put(
        f"{API}/admin/telegram",
        headers=admin_auth["headers"],
        json={"enabled": True, "bot_token": TOKEN, "channel_id": "@brend_zayavki"},
    )

    async def card(row, site_url):
        return b"\x89PNG not really"

    original = telegram.call_bot

    async def refuse_photos(token, method, payload=None, files=None):
        if method == "sendPhoto":
            raise telegram.TelegramError("Bad Request: IMAGE_PROCESS_FAILED")
        return await original(token, method, payload, files)

    monkeypatch.setattr(telegram, "build_card", card)
    monkeypatch.setattr(telegram, "call_bot", refuse_photos)
    rfq_id = await _request(client, buyer_auth, "Заявка с непринятой картинкой")
    await asyncio.gather(*list(telegram._background))
    texts = [p for m, p in telegram_calls if m == "sendMessage"]
    assert len(texts) == 1 and "Заявка с непринятой картинкой" in texts[0]["text"]
    post = await fetchrow("SELECT status FROM telegram_posts WHERE rfq_id = $1", rfq_id)
    assert post["status"] == "sent"


def test_the_card_draws_without_a_logo_and_cuts_long_text():
    if not telegram_card.fonts_available():
        pytest.skip("No Cyrillic font on this machine")
    lines = [
        ("budget", "12 000–24 600 TJS"),
        ("quantity", "40 шт"),
        ("proposals", "Приём КП до 22.11.2026, 23:59"),
        ("deadline", "Дедлайн до 05.12.2026, 23:59"),
        ("place", "Душанбе"),
    ]
    for logo, company in ((b"not an image", True), (None, False)):
        png = telegram_card.render_request_card(
            telegram_card.CardData(
                title="Очень длинное название заявки " * 6,
                buyer_name="Общество с ограниченной ответственностью «Сомон Трейд»",
                buyer_logo=logo,
                buyer_is_company=company,
                lines=lines,
            )
        )
        image = Image.open(io.BytesIO(png))
        # Two title lines and five facts is the tallest card.
        assert image.width == 1080 and 560 <= image.height <= 760


@pytest.mark.asyncio
async def test_the_admin_sees_a_preview_of_the_next_post(client, admin_auth, telegram_calls):
    res = await client.get(f"{API}/admin/telegram/preview", headers=admin_auth["headers"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert "🏢 Заказчик: " in body["caption"]
    assert "⏳ <b>Приём КП до " in body["caption"]
    assert "Откликнуться" not in body["caption"]
    if telegram_card.fonts_available():
        assert body["image"].startswith("data:image/png;base64,")
