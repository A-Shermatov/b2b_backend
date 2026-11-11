"""The bot as an assistant in private chats: requests and answers inside Telegram.

A person links their account once (see links.py). After that:

  * a supplier opens the feed of buyers' requests as a widget in the chat,
    leafs through it and sends an offer — price and days — without leaving
    Telegram;
  * a buyer writes to the bot what they need; the bot answers with a widget
    to pick the kind, the category and how long to take offers, and publishes
    the request;
  * every notification of the platform is repeated to the linked chat.

Everything goes through the same services as the site, with the same
permission checks: the bot only stands in for the browser.

The bot reads its updates by long polling (getUpdates), so the server needs
no public address. One process polls at a time (a database lock decides
which); the place it stopped at is kept in telegram_settings.update_offset.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.core.exceptions import AppError
from app.core.permissions import ensure_company_role_permission, ensure_permission, fetch_permission_names
from app.database import pool
from app.deps import _load_membership, actor_from_row, user_from_row
from app.modules.actors.services import ActorService
from app.modules.auth.services.common import load_user
from app.modules.proposals.schemas import ProposalCreate
from app.modules.proposals.services import ProposalService
from app.modules.rfqs.schemas import ProductRfqCreate, ServiceRfqCreate
from app.modules.rfqs.services import RfqService
from app.modules.telegram import links, widgets
from app.modules.telegram import service as telegram
from app.modules.telegram.service import TelegramError

logger = logging.getLogger(__name__)

POLL_SECONDS = 25
IDLE_SECONDS = 15
LOCK_KEY = 0x62_6D_74_67  # "bmtg": one poller per database
CITIES = ["Душанбе", "Худжанд", "Бохтар", "Куляб", "Другой город"]
DAYS_CHOICES = (3, 7, 14, 30)
MAX_TEXT = 3000
TITLE_LENGTH = 60

HELP = (
    "<b>Brand Market в Telegram</b>\n"
    "• /zayavki — заявки покупателей: листайте и отвечайте ценой прямо здесь.\n"
    "• Чтобы разместить заявку, просто напишите мне, что вам нужно.\n"
    "• Уведомления с площадки приходят в этот чат."
)
NOT_LINKED = (
    "Чтобы работать с заявками здесь, подключите аккаунт Brand Market: "
    "откройте на сайте «Мой профиль» → «Telegram» и нажмите «Подключить»."
)


class Refusal(Exception):
    """The person may not do this; the text is for them."""


def _reason(exc: Exception) -> str:
    if isinstance(exc, Refusal):
        return str(exc)
    if isinstance(exc, AppError):
        detail = str(exc.detail or "")
        # Service messages are written for people when they are in Russian.
        return detail if re.search("[А-Яа-я]", detail) else "Сейчас это действие недоступно"
    return "Не получилось, попробуйте ещё раз"


# --- who is acting ------------------------------------------------------------


async def _context(user_id: int, side: str, permission: str):
    """The person's profile on one side, checked the way the API checks it."""
    async with pool.connection() as conn:
        row = await load_user(conn, user_id)
        if not row or row["status"] == "blocked":
            raise Refusal("Аккаунт недоступен. Напишите в поддержку на сайте.")
        row["permissions"] = await fetch_permission_names(
            conn, role_id=row.get("role_id"), role_name=row["role"]
        )
    user = user_from_row(row)
    if user.role not in (side, "both"):
        raise Refusal(
            "У вас нет кабинета поставщика: откройте его на сайте."
            if side == "supplier"
            else "У вас нет кабинета покупателя: откройте его на сайте."
        )
    actor_row = await ActorService().resolve_actor_id(user.id, None, None, side, strict=True)
    if not actor_row:
        raise Refusal("Профиль не найден. Зайдите на сайт и выберите, от чьего имени работаете.")
    actor = actor_from_row(actor_row)
    company_role = None
    if actor.company_id:
        membership = await _load_membership(user.id, actor.company_id, actor.side)
        if not membership:
            raise Refusal("У вас больше нет доступа к компании.")
        company_role = membership[1]
    try:
        ensure_permission(user, permission)
        ensure_company_role_permission(actor.kind, company_role, permission)
    except AppError as exc:
        raise Refusal("У вас нет прав на это действие в компании.") from exc
    return user, actor


async def _category_name(conn, key: str | None) -> str | None:
    if not key:
        return None
    return await conn.fetchval(
        "SELECT name FROM categories WHERE slug = $1 OR id::text = $1 LIMIT 1", str(key)
    )


def _short_no(rfq_id: str) -> str:
    digits = re.sub(r"\D", "", rfq_id)
    return f"З‑{(digits or rfq_id)[-4:]}"


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --- the supplier's feed --------------------------------------------------------


async def feed_item(user_id: int, index: int) -> dict:
    """One request of the supplier's feed, by its place; {"n": 0} when there are none."""
    _, actor = await _context(user_id, "supplier", "rfqs.view")
    index = max(0, int(index))
    page = await RfqService().list_board_for_supplier(actor.id, page=index + 1, page_size=1)
    total = int(page["total"])
    if total == 0:
        return {"n": 0}
    if not page["items"]:
        # The feed got shorter while the person was reading it.
        index = 0
        page = await RfqService().list_board_for_supplier(actor.id, page=1, page_size=1)
        if not page["items"]:
            return {"n": 0}
    rfq = page["items"][0]
    async with pool.connection() as conn:
        category = await _category_name(conn, rfq.get("category_id"))
    buyer = rfq.get("buyer") or {}
    kind = f"товар, {rfq['quantity']} шт" if rfq["type"] == "product" else "услуга"
    budget_from = rfq.get("budget_from")
    return {
        "i": index,
        "n": total,
        "id": rfq["id"],
        "no": _short_no(rfq["id"]),
        "t": _clip(rfq["title"], 90),
        "c": _clip(category, 40),
        "b": telegram._budget(rfq),
        "d": telegram._until(rfq["deadline"]) or "",
        "k": kind,
        "by": _clip(buyer.get("display_name") or "Покупатель", 40),
        "m": 1 if rfq.get("my_proposal_status") else 0,
        "p": 1 if rfq.get("promoted") else 0,
        "cur": rfq.get("currency") or "TJS",
        "bf": int(budget_from) if budget_from and rfq.get("budget_type") == "fixed" else 0,
    }


async def submit_offer(user_id: int, rfq_id: str, price: str, days: str) -> dict:
    user, actor = await _context(user_id, "supplier", "proposals.create")
    try:
        amount = float(int(price))
        term = int(days)
    except ValueError as exc:
        raise Refusal("Укажите цену и срок числами.") from exc
    if amount <= 0 or not 1 <= term <= 365:
        raise Refusal("Цена должна быть больше нуля, срок — от 1 до 365 дней.")
    # Loaded as the supplier: a request they may not see is refused here.
    rfq = await RfqService().get(rfq_id, supplier_id=actor.id)
    currency = rfq.get("currency")
    await ProposalService().submit(
        rfq_id,
        actor.id,
        ProposalCreate(price=amount, currency=currency or "TJS", delivery_time=str(term), message=None),
        user.id,
    )
    return {"ok": 1}


# --- the buyer's new request ------------------------------------------------------


async def _top_categories(conn) -> list[list[str]]:
    rows = await conn.fetch(
        "SELECT slug, name FROM categories WHERE parent_id IS NULL ORDER BY id LIMIT 12"
    )
    return [[row["slug"], row["name"]] for row in rows]


def _draft_data(draft_id: str, title: str, categories: list[list[str]]) -> str:
    """The widget's starting state, cut to what Telegram accepts (1024 bytes)."""
    cats = [[slug, _clip(name, 28)] for slug, name in categories]
    while True:
        data = json.dumps(
            {"id": draft_id, "t": title, "c": cats, "g": CITIES}, ensure_ascii=False, separators=(",", ":")
        )
        if len(data.encode("utf-8")) <= widgets.DATA_LIMIT or not cats:
            return data
        cats.pop()


async def start_request(user_id: int, text: str) -> dict:
    """Keep what the buyer wrote and give the widget what it needs to finish the request."""
    await _context(user_id, "buyer", "rfqs.create")
    text = text.strip()[:MAX_TEXT]
    if len(text) < 5:
        raise Refusal("Напишите чуть подробнее, что вам нужно, — хотя бы несколько слов.")
    title = _clip(text.splitlines()[0], TITLE_LENGTH)
    draft_id = secrets.token_hex(5)
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM telegram_drafts WHERE created_at < NOW() - INTERVAL '2 days' AND rfq_id IS NULL"
        )
        await conn.execute(
            "INSERT INTO telegram_drafts (id, user_id, text) VALUES ($1, $2, $3)", draft_id, user_id, text
        )
        categories = await _top_categories(conn)
    return {"id": draft_id, "title": title, "chat_data": _draft_data(draft_id, title, categories)}


def _day(offset_days: int) -> str:
    """A calendar day of the market, `offset_days` from today."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        now = datetime.now(ZoneInfo(get_settings().business_timezone))
    except ZoneInfoNotFoundError:
        # No zone database on this machine (Windows without tzdata): Dushanbe is UTC+5 all year.
        now = datetime.now(timezone(timedelta(hours=5)))
    return (now + timedelta(days=offset_days)).date().isoformat()


async def publish_request(
    user_id: int, draft_id: str, kind: str, category: str, days: str, city_index: str
) -> dict:
    user, actor = await _context(user_id, "buyer", "rfqs.create")
    await _context(user_id, "buyer", "rfqs.publish")
    async with pool.connection() as conn:
        draft = await conn.fetchrow(
            "SELECT id, text, rfq_id FROM telegram_drafts WHERE id = $1 AND user_id = $2", draft_id, user_id
        )
        if not draft:
            raise Refusal("Эта заявка устарела. Напишите мне ещё раз, что вам нужно.")
        if draft["rfq_id"]:
            # A second tap on "Опубликовать", whatever is chosen now: the request is already there.
            return {"ok": 1, "no": _short_no(draft["rfq_id"]), "id": draft["rfq_id"]}
        known_category = await _category_name(conn, category)
    if kind not in ("s", "p"):
        raise Refusal("Выберите, что нужно: товар или услуга.")
    try:
        term = int(days)
        city = CITIES[int(city_index)]
    except (ValueError, IndexError) as exc:
        raise Refusal("Не удалось прочитать выбор, попробуйте ещё раз.") from exc
    if term not in DAYS_CHOICES:
        raise Refusal("Выберите срок приёма предложений.")
    if not known_category:
        raise Refusal("Выберите категорию.")
    text = draft["text"]
    deadline = _day(term)
    common = {
        "title": _clip(text.splitlines()[0], 120),
        "description": text,
        "category_id": category,
        "budget_type": "open",
        "currency": "TJS",
        "deadline": deadline,
        "visibility": "public",
        "status": "draft",
        "payment_mode": "escrow",
    }
    if kind == "p":
        data: Any = ProductRfqCreate(
            **common,
            quantity=1,
            delivery_country="Таджикистан",
            delivery_city="Уточняется" if city == CITIES[-1] else city,
            # The same day as the request deadline means "not set": the site hides it.
            delivery_date=deadline,
        )
    else:
        data = ServiceRfqCreate(**common, project_duration="по договорённости", start_date=deadline)
    created_by = f"{user.first_name} {user.last_name}".strip()
    created = await RfqService().create(actor.id, created_by, data, user.id)
    rfq_id = created["id"] if isinstance(created, dict) else created.id
    async with pool.connection() as conn:
        await conn.execute("UPDATE telegram_drafts SET rfq_id = $2 WHERE id = $1", draft_id, rfq_id)
    await RfqService().publish(rfq_id, actor.id)
    return {"ok": 1, "no": _short_no(rfq_id), "id": rfq_id}


# --- talking to Telegram -------------------------------------------------------------


async def _say(token: str, chat_id: int, text: str, **extra: Any) -> None:
    await telegram.call_bot(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **extra},
    )


async def _send_widget(token: str, chat_id: int, widget: str, chat_data: str, fallback: str) -> None:
    try:
        await telegram.call_bot(
            token,
            "sendWidget",
            {"chat_id": chat_id, "html": widget, "chat_data": chat_data, "allow_queries": True},
        )
    except TelegramError as exc:
        # An old Telegram server or app: say so instead of going silent.
        logger.warning("Telegram refused a widget: %s", exc)
        await _say(token, chat_id, fallback)


async def send_feed(token: str, chat_id: int, user_id: int, site_url: str) -> None:
    first = await feed_item(user_id, 0)
    await _send_widget(
        token,
        chat_id,
        widgets.FEED_WIDGET,
        json.dumps(first, ensure_ascii=False, separators=(",", ":")),
        f"Заявки покупателей открыты на сайте: {site_url}/supplier/rfqs",
    )


async def handle_message(state: dict, message: dict) -> None:
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    if chat.get("type") != "private" or sender.get("is_bot"):
        return
    token, chat_id = state["token"], chat["id"]
    text = (message.get("text") or "").strip()
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()

    if command == "/start" and argument.strip():
        account = await links.redeem(argument.strip(), sender, chat_id)
        if not account:
            await _say(
                token,
                chat_id,
                "Ссылка для подключения устарела. Откройте на сайте «Мой профиль» → «Telegram» "
                "и нажмите «Подключить» ещё раз.",
            )
            return
        name = html.escape(f"{account['first_name']} {account['last_name']}".strip())
        await _say(token, chat_id, f"✅ Аккаунт <b>{name}</b> подключён.\n\n{HELP}")
        return

    user_id = await links.user_of_telegram(sender.get("id") or 0)
    if user_id is None:
        await _say(
            token,
            chat_id,
            NOT_LINKED,
            reply_markup={
                "inline_keyboard": [[{"text": "Открыть профиль на сайте", "url": f"{state['site_url']}/login"}]]
            },
        )
        return

    try:
        if command in ("/start", "/help", "/menu"):
            await _say(token, chat_id, HELP)
        elif command in ("/zayavki", "/requests"):
            await send_feed(token, chat_id, user_id, state["site_url"])
        elif text.startswith("/"):
            await _say(token, chat_id, "Не знаю такой команды.\n\n" + HELP)
        elif not text:
            await _say(token, chat_id, "Напишите текстом, что вам нужно, — я оформлю заявку.")
        else:
            draft = await start_request(user_id, text)
            await _send_widget(
                token,
                chat_id,
                widgets.REQUEST_WIDGET,
                draft["chat_data"],
                f"Оформить заявку можно на сайте: {state['site_url']}/customer/rfqs/new",
            )
    except (Refusal, AppError) as exc:
        await _say(token, chat_id, html.escape(_reason(exc)))


async def answer_query(user_id: int | None, data: str) -> dict:
    """What a widget asked, as the answer it gets. Never raises."""
    if user_id is None:
        return {"e": "Аккаунт не подключён. Откройте на сайте «Мой профиль» → «Telegram»."}
    parts = (data or "").split(":")
    try:
        if parts[0] == "f" and len(parts) == 2 and parts[1].isdigit():
            return await feed_item(user_id, int(parts[1]))
        if parts[0] == "o" and len(parts) == 4:
            return await submit_offer(user_id, parts[1], parts[2], parts[3])
        if parts[0] == "p" and len(parts) == 6:
            return await publish_request(user_id, parts[1], parts[2], parts[3], parts[4], parts[5])
        return {"e": "Эта кнопка больше не работает. Откройте заявки заново: /zayavki"}
    except (Refusal, AppError) as exc:
        return {"e": _reason(exc)}
    except Exception:  # noqa: BLE001 — the widget must get an answer within 15 seconds
        logger.exception("Telegram widget query failed: %s", parts[0] if parts else "")
        return {"e": "Не получилось, попробуйте ещё раз"}


async def handle_widget_query(state: dict, query: dict) -> None:
    sender = query.get("from") or {}
    user_id = await links.user_of_telegram(sender.get("id") or 0)
    answer = await answer_query(user_id, query.get("data") or "")
    await telegram.call_bot(
        state["token"],
        "answerWidgetQuery",
        {
            "widget_query_id": query["id"],
            "data": json.dumps(answer, ensure_ascii=False, separators=(",", ":")),
        },
    )


async def handle_update(state: dict, update: dict) -> None:
    try:
        if "widget_query" in update:
            await handle_widget_query(state, update["widget_query"])
        elif "message" in update:
            await handle_message(state, update["message"])
    except TelegramError as exc:
        logger.warning("Telegram refused an answer: %s", exc)
    except Exception:  # noqa: BLE001 — one bad update must not stop the bot
        logger.exception("Telegram update %s failed", update.get("update_id"))


# --- notifications of the platform, repeated to the chat ---------------------------------


async def mirror_notification(user_id: int, title: str, body: str, href: str | None) -> None:
    try:
        state = await links.assistant_state()
        if not state["enabled"]:
            return
        chat_id = await links.chat_of_user(user_id)
        if chat_id is None:
            return
        text = f"<b>{html.escape(title)}</b>\n{html.escape(body)}"
        extra: dict[str, Any] = {}
        if href and href.startswith("/"):
            extra["reply_markup"] = {
                "inline_keyboard": [[{"text": "Открыть на сайте", "url": f"{state['site_url']}{href}"}]]
            }
        await _say(state["token"], chat_id, text, **extra)
    except TelegramError as exc:
        logger.info("Telegram notification not delivered to user %s: %s", user_id, exc)
    except Exception:  # noqa: BLE001 — a notification must never break what caused it
        logger.exception("Telegram notification failed for user %s", user_id)


_background: set[asyncio.Task] = set()


def schedule_notification(user_id: int, title: str, body: str, href: str | None) -> None:
    """Fire and forget; outside a running loop (scripts) it is simply skipped."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(mirror_notification(user_id, title, body, href))
    _background.add(task)
    task.add_done_callback(_background.discard)


# --- reading updates ------------------------------------------------------------------------


async def poll_once(state: dict) -> int:
    """Read one batch of updates and handle it; returns how many came."""
    updates = await telegram.call_bot(
        state["token"],
        "getUpdates",
        {
            "offset": state["update_offset"],
            "timeout": POLL_SECONDS,
            "allowed_updates": ["message", "widget_query"],
        },
        timeout=POLL_SECONDS + 10,
    )
    for update in updates or []:
        await handle_update(state, update)
        state["update_offset"] = int(update["update_id"]) + 1
    if updates:
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE telegram_settings SET update_offset = $1 WHERE id = 1", state["update_offset"]
            )
    return len(updates or [])


async def run_forever() -> None:
    """The bot's reading loop. Sleeps while the assistant is off; never raises."""
    while True:
        try:
            state = await links.assistant_state()
            if not state["enabled"]:
                await asyncio.sleep(IDLE_SECONDS)
                continue
            async with pool.acquire() as lock_conn:
                # One poller per database: a second process waits its turn here.
                if not await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
                    await asyncio.sleep(IDLE_SECONDS)
                    continue
                try:
                    while True:
                        fresh = await links.assistant_state()
                        if not fresh["enabled"] or fresh["token"] != state["token"]:
                            break
                        state["site_url"] = fresh["site_url"]
                        await poll_once(state)
                finally:
                    await lock_conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        except asyncio.CancelledError:
            raise
        except TelegramError as exc:
            logger.warning("Telegram updates are not coming: %s", exc)
            await asyncio.sleep(IDLE_SECONDS * 2)
        except Exception:  # noqa: BLE001 — the loop outlives any single failure
            logger.exception("Telegram assistant loop failed; retrying")
            await asyncio.sleep(IDLE_SECONDS * 2)


_runner: asyncio.Task | None = None


def start() -> None:
    global _runner
    if _runner is None or _runner.done():
        _runner = asyncio.get_running_loop().create_task(run_forever())


async def stop() -> None:
    global _runner
    if _runner is not None:
        _runner.cancel()
        try:
            await _runner
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _runner = None
