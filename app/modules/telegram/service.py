"""Posting new requests to a Telegram channel through the platform's bot.

The admin sets the bot token and the channel in the admin panel. Every public
request, once published, becomes one post: a picture card (buyer's logo,
Brend Market, the title and key facts) with a short caption and a button that
opens it on the site. The token is stored encrypted and never sent back.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.exceptions import ValidationError
from app.database import pool
from app.modules.telegram.card import CardData, CardUnavailable, render_request_card
from app.utils.audit import log_event
from app.utils.storage import files_base_url, storage_key_from_url

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,64}$")
CHANNEL_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{6,16}|-?\d{5,16})$")
# A logo bigger than this is not worth waiting for: the card shows a grey icon.
MAX_LOGO_BYTES = 3 * 1024 * 1024


class TelegramError(Exception):
    """Telegram refused a call; the message is safe to show to an admin."""


# --- the token box -----------------------------------------------------------


def _fernet() -> Fernet:
    settings = get_settings()
    material = f"telegram:{settings.secret_key}".encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(stored: str | None) -> str | None:
    if not stored:
        return None
    try:
        return _fernet().decrypt(stored.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Telegram bot token cannot be decrypted: SECRET_KEY changed?")
        return None


def mask_token(token: str | None) -> str | None:
    if not token:
        return None
    return f"{token.split(':', 1)[0]}:••••{token[-4:]}"


# --- the Telegram Bot API ----------------------------------------------------


async def call_bot(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> Any:
    """One Bot API call. Tests replace this function; nothing else talks to Telegram.

    With `files` the call goes as a form upload (a photo); nested values such
    as the button keyboard are then sent as JSON text, as Telegram expects.
    """
    url = f"{API_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0) if files else TIMEOUT) as client:
        try:
            if files:
                form = {
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                    for key, value in (payload or {}).items()
                }
                response = await client.post(url, data=form, files=files)
            else:
                response = await client.post(url, json=payload or {})
        except httpx.HTTPError as exc:
            raise TelegramError("Telegram не отвечает, попробуйте позже") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise TelegramError(f"Telegram вернул ошибку {response.status_code}") from exc
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram отклонил запрос")
    return body.get("result")


# --- the post ----------------------------------------------------------------


def _money(value: Any) -> str:
    amount = float(value)
    text = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", " ")


def _budget(row) -> str:
    currency = row["currency"] or "TJS"
    kind = row["budget_type"]
    if kind == "range" and row["budget_from"] and row["budget_to"]:
        return f"{_money(row['budget_from'])}–{_money(row['budget_to'])} {currency}"
    if kind in ("fixed", "range") and row["budget_from"]:
        return f"{_money(row['budget_from'])} {currency}"
    return "цена по предложениям"


def request_url(site_url: str, rfq_id: str) -> str:
    return f"{site_url.rstrip('/')}/supplier/rfqs/{rfq_id}"


def buyer_name(row) -> str:
    """A company is named; a private person is not, in a public channel."""
    if row["buyer_kind"] == "company" and (row["company_title"] or "").strip():
        return row["company_title"].strip()
    return "Частный заказчик"


def _moment(value: Any) -> datetime | None:
    """A request date as stored: '2026-11-11', or with a time ('2026-11-11T18:00').

    A date without a time lasts to the end of that day: the request expires
    only when the next day starts.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time(23, 59))
    text = str(value).strip().replace(" ", "T", 1)
    try:
        if len(text) > 10:
            return datetime.fromisoformat(text[:16])
        return datetime.combine(date.fromisoformat(text[:10]), time(23, 59))
    except ValueError:
        return None


def _until(value: Any) -> str | None:
    """'11.11.2026, 23:59'."""
    moment = _moment(value)
    return f"{moment:%d.%m.%Y, %H:%M}" if moment else None


def _from(value: Any) -> str | None:
    """'25.11.2026': a start has no time of day."""
    moment = _moment(value)
    return f"{moment:%d.%m.%Y}" if moment else None


def card_lines(row) -> list[tuple[str, str]]:
    """The lines under the title on the picture, most important first."""
    budget = _budget(row)
    lines = [("budget", budget[:1].upper() + budget[1:])]
    if row["type"] == "product" and row["quantity"]:
        lines.append(("quantity", f"{row['quantity']} шт"))
    proposals = _until(row["deadline"])
    if proposals:
        lines.append(("proposals", f"Приём КП до {proposals}"))
    if row["type"] == "product":
        due = _until(row["delivery_date"])
        if due:
            lines.append(("deadline", f"Дедлайн до {due}"))
        if row["delivery_city"]:
            lines.append(("place", row["delivery_city"]))
    else:
        start = _from(row["start_date"])
        if start:
            lines.append(("deadline", f"Начало работ с {start}"))
    return lines


def format_post(row, site_url: str) -> str:
    """The caption under the picture, in the shape of a job post.

    Title, category and who asks; amount, money and how it is paid; the two
    dates in bold. The request opens with the button under the post, so the
    caption carries no link. Everything the buyer typed is escaped.
    """
    e = html.escape
    lines = [f"🆕 <b>{e(row['title'])}</b>"]
    if row["category_name"]:
        lines.append(f"🗂 {e(row['category_name'])}")
    lines.append(f"🏢 Заказчик: <b>{e(buyer_name(row))}</b>")
    lines.append("")
    if row["type"] == "product" and row["quantity"]:
        lines.append(f"📦 Количество: {row['quantity']} шт")
    lines.append(f"💰 Бюджет: {_budget(row)}")
    if row["payment_mode"] == "direct":
        lines.append("⚡ Оплата сразу поставщику")
    else:
        lines.append("🛡 Оплата с гарантией площадки")
    if row["type"] == "product" and row["delivery_city"]:
        lines.append(f"📍 Город: {e(row['delivery_city'])}")
    dates = []
    proposals = _until(row["deadline"])
    if proposals:
        dates.append(f"⏳ <b>Приём КП до {proposals}</b>")
    if row["type"] == "product":
        due = _until(row["delivery_date"])
        if due:
            dates.append(f"📅 <b>Дедлайн: до {due}</b>")
    else:
        start = _from(row["start_date"])
        if start:
            dates.append(f"📅 <b>Начало работ: с {start}</b>")
        duration = (row["project_duration"] or "").strip()
        if duration:
            dates.append(f"⏱ Срок работ: {e(duration)}")
    if dates:
        lines.append("")
        lines.extend(dates)
    return "\n".join(lines)


def _logo_bytes(stored: str | None) -> bytes | None:
    """The buyer's logo from our own storage: the local folder, or our files URL.

    Never any other address: the logo field is client data, and fetching whatever
    it names would let a company make the server request internal URLs (SSRF).
    """
    if not stored:
        return None
    key = storage_key_from_url(stored)
    if not key or key.startswith(("http://", "https://")) or ".." in key.split("/"):
        return None
    root = Path(get_settings().local_upload_dir).resolve()
    local = (root / key).resolve()
    if local.is_relative_to(root) and local.is_file():
        if local.stat().st_size <= MAX_LOGO_BYTES:
            return local.read_bytes()
        return None
    url = f"{files_base_url().rstrip('/')}/{key}"
    if not url.startswith(("http://", "https://")):
        return None
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False) as client:
            response = client.get(url)
        if response.status_code == 200 and len(response.content) <= MAX_LOGO_BYTES:
            return response.content
    except httpx.HTTPError:
        return None
    return None


def _render_card(row, site_url: str) -> bytes:
    company = row["buyer_kind"] == "company"
    return render_request_card(
        CardData(
            title=row["title"],
            buyer_name=buyer_name(row),
            buyer_logo=_logo_bytes(row["company_logo"]) if company else None,
            buyer_is_company=company,
            lines=card_lines(row),
        )
    )


async def build_card(row, site_url: str) -> bytes | None:
    """The picture for a post, or None when this server cannot draw one."""
    try:
        return await asyncio.to_thread(_render_card, row, site_url)
    except CardUnavailable:
        logger.warning("No Cyrillic font for Telegram cards: posting text only")
    except Exception:
        logger.exception("Telegram card for rfq %s could not be drawn", row["id"])
    return None


POST_ROW_SQL = """
    SELECT r.id, r.title, r.type::text AS type, r.status::text AS status,
           r.visibility::text AS visibility, r.budget_type::text AS budget_type,
           r.budget_from, r.budget_to, r.currency, r.deadline, r.quantity,
           r.delivery_city, r.delivery_date, r.start_date, r.project_duration,
           r.payment_mode,
           (
               SELECT c.name FROM categories c
               WHERE c.slug = r.category_id OR c.id::text = r.category_id
               LIMIT 1
           ) AS category_name,
           a.kind::text AS buyer_kind,
           co.title AS company_title,
           co.logo AS company_logo
    FROM rfqs r
    LEFT JOIN actors a ON a.id = r.actor_id
    LEFT JOIN companies co ON co.id = a.company_id
    WHERE r.id = $1
"""


# --- settings ----------------------------------------------------------------


async def _load(conn) -> dict:
    row = await conn.fetchrow("SELECT * FROM telegram_settings WHERE id = 1")
    return dict(row) if row else {"enabled": False}


def _site_url(settings_row: dict) -> str:
    return (settings_row.get("site_url") or get_settings().frontend_url).rstrip("/")


async def admin_view() -> dict:
    async with pool.connection() as conn:
        row = await _load(conn)
        posts = await conn.fetch(
            """
            SELECT p.rfq_id, p.status, p.error, p.message_id, p.created_at, r.title
            FROM telegram_posts p
            LEFT JOIN rfqs r ON r.id = p.rfq_id
            ORDER BY p.created_at DESC
            LIMIT 20
            """
        )
        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'sent')::int AS sent,
                COUNT(*) FILTER (WHERE status = 'failed')::int AS failed
            FROM telegram_posts
            """
        )
    token = decrypt_token(row.get("bot_token_enc"))
    return {
        "enabled": bool(row.get("enabled")),
        "has_token": bool(token),
        "token_hint": mask_token(token),
        "bot_username": row.get("bot_username"),
        "channel_id": row.get("channel_id"),
        "channel_title": row.get("channel_title"),
        "site_url": _site_url(row),
        "default_site_url": get_settings().frontend_url.rstrip("/"),
        "updated_at": row.get("updated_at"),
        "sent_count": stats["sent"],
        "failed_count": stats["failed"],
        "recent": [dict(p) for p in posts],
    }


async def update_settings(data: dict, user_id: int) -> dict:
    """Save what the admin entered; a new token and channel are checked with Telegram first."""
    async with pool.connection() as conn:
        current = await _load(conn)
    token = (data.get("bot_token") or "").strip() or decrypt_token(current.get("bot_token_enc"))
    channel = (data.get("channel_id") or "").strip() or None
    site_url = (data.get("site_url") or "").strip().rstrip("/") or None
    enabled = bool(data.get("enabled"))

    if data.get("bot_token") and not TOKEN_RE.match(token or ""):
        raise ValidationError("Токен бота выглядит неверно: его выдаёт @BotFather, вид 123456:ABC…")
    if channel and not CHANNEL_RE.match(channel):
        raise ValidationError("Канал: укажите @имя_канала или числовой id вида -100…")
    if site_url and not re.match(r"^https?://[^\s/]+", site_url):
        raise ValidationError("Адрес сайта должен начинаться с http:// или https://")
    if enabled and not (token and channel):
        raise ValidationError("Чтобы включить отправку, укажите токен бота и канал")

    username = current.get("bot_username")
    channel_title = current.get("channel_title")
    if token and (data.get("bot_token") or not username):
        try:
            me = await call_bot(token, "getMe")
        except TelegramError as exc:
            raise ValidationError(f"Бот не отвечает: {exc}") from exc
        username = me.get("username")
    if token and channel and channel != current.get("channel_id"):
        try:
            chat = await call_bot(token, "getChat", {"chat_id": channel})
        except TelegramError as exc:
            raise ValidationError(
                f"Канал не найден или бот не добавлен в него администратором: {exc}"
            ) from exc
        channel_title = chat.get("title") or chat.get("username") or channel

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO telegram_settings (
                id, enabled, bot_token_enc, bot_username, channel_id, channel_title,
                site_url, updated_by, updated_at
            )
            VALUES (1, $1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                bot_token_enc = EXCLUDED.bot_token_enc,
                bot_username = EXCLUDED.bot_username,
                channel_id = EXCLUDED.channel_id,
                channel_title = EXCLUDED.channel_title,
                site_url = EXCLUDED.site_url,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            """,
            enabled,
            encrypt_token(token) if token else None,
            username,
            channel,
            channel_title if channel else None,
            site_url,
            user_id,
        )
        await log_event(
            conn,
            "telegram.settings.update",
            "telegram_settings",
            1,
            user_id=user_id,
            # Never the token itself.
            details={
                "enabled": enabled,
                "channel_id": channel,
                "token_changed": bool(data.get("bot_token")),
                "site_url": site_url,
            },
        )
    return await admin_view()


async def send_test(user_id: int) -> dict:
    async with pool.connection() as conn:
        current = await _load(conn)
    token = decrypt_token(current.get("bot_token_enc"))
    channel = current.get("channel_id")
    if not (token and channel):
        raise ValidationError("Сначала сохраните токен бота и канал")
    text = (
        "✅ <b>Brend Market на связи</b>\n"
        "Сюда будут приходить новые заявки покупателей."
    )
    try:
        await call_bot(
            token,
            "sendMessage",
            {"chat_id": channel, "text": text, "parse_mode": "HTML", "disable_notification": True},
        )
    except TelegramError as exc:
        raise ValidationError(f"Не удалось отправить: {exc}") from exc
    async with pool.connection() as conn:
        await log_event(conn, "telegram.test", "telegram_settings", 1, user_id=user_id)
    return {"ok": True}


# --- posting requests --------------------------------------------------------


async def post_request(rfq_id: str, *, force: bool = False) -> str:
    """Post one request to the channel. Returns sent, failed, skipped or duplicate."""
    async with pool.connection() as conn:
        current = await _load(conn)
        if not current.get("enabled") and not force:
            return "skipped"
        token = decrypt_token(current.get("bot_token_enc"))
        channel = current.get("channel_id")
        if not (token and channel):
            return "skipped"
        row = await conn.fetchrow(POST_ROW_SQL, rfq_id)
        # Only open requests that every supplier may see go to a public channel.
        if (
            not row
            or row["visibility"] != "public"
            or row["status"] not in ("published", "receiving_proposals")
        ):
            return "skipped"
        if not force and await conn.fetchval(
            "SELECT 1 FROM telegram_posts WHERE rfq_id = $1 AND status = 'sent'", rfq_id
        ):
            return "duplicate"
        site_url = _site_url(current)

    caption = format_post(row, site_url)
    keyboard = {"inline_keyboard": [[{"text": "Открыть заявку", "url": request_url(site_url, rfq_id)}]]}
    card = await build_card(row, site_url)
    status, error, message_id = "sent", None, None
    try:
        result = None
        if card:
            try:
                result = await call_bot(
                    token,
                    "sendPhoto",
                    {"chat_id": channel, "caption": caption, "parse_mode": "HTML", "reply_markup": keyboard},
                    files={"photo": ("request.png", card, "image/png")},
                )
            except TelegramError as exc:
                # A picture Telegram does not take must not cost the post: send the text.
                logger.warning("Telegram photo for rfq %s refused, sending text: %s", rfq_id, exc)
        if result is None:
            result = await call_bot(
                token,
                "sendMessage",
                {
                    "chat_id": channel,
                    "text": caption,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": keyboard,
                },
            )
        message_id = result.get("message_id") if isinstance(result, dict) else None
    except TelegramError as exc:
        status, error = "failed", str(exc)[:500]
        logger.warning("Telegram post for rfq %s failed: %s", rfq_id, exc)

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO telegram_posts (rfq_id, chat_id, message_id, status, error, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (rfq_id) DO UPDATE SET
                chat_id = EXCLUDED.chat_id,
                message_id = EXCLUDED.message_id,
                status = EXCLUDED.status,
                error = EXCLUDED.error,
                created_at = NOW()
            """,
            rfq_id,
            channel,
            message_id,
            status,
            error,
        )
    return status


SAMPLE_ROW = {
    "id": "00000000-0000-0000-0000-000000004821",
    "title": "Ноутбуки для отдела продаж",
    "type": "product",
    "budget_type": "range",
    "budget_from": 120000,
    "budget_to": 150000,
    "currency": "TJS",
    "deadline": None,
    "quantity": 20,
    "delivery_city": "Душанбе",
    "delivery_date": None,
    "start_date": None,
    "project_duration": None,
    "payment_mode": "escrow",
    "category_name": "Компьютеры и оргтехника",
    "buyer_kind": "company",
    "company_title": "ООО «Пример»",
    "company_logo": None,
}


async def preview() -> dict:
    """What the next post looks like: the newest open public request, or a sample."""
    async with pool.connection() as conn:
        current = await _load(conn)
        rfq_id = await conn.fetchval(
            """
            SELECT id FROM rfqs
            WHERE visibility::text = 'public'
              AND status::text IN ('published', 'receiving_proposals')
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = await conn.fetchrow(POST_ROW_SQL, rfq_id) if rfq_id else None
    site_url = _site_url(current)
    if row is None:
        soon = date.today() + timedelta(days=12)
        row = {**SAMPLE_ROW, "deadline": soon.isoformat(), "delivery_date": (soon + timedelta(days=13)).isoformat()}
    card = await build_card(row, site_url)
    return {
        "caption": format_post(row, site_url),
        "image": f"data:image/png;base64,{base64.b64encode(card).decode()}" if card else None,
        "sample": rfq_id is None,
    }


async def post_request_safely(rfq_id: str) -> None:
    """The request is already published: a channel failure must not turn it into an error."""
    try:
        await post_request(rfq_id)
    except Exception:
        logger.exception("Telegram post for rfq %s crashed", rfq_id)


_background: set[asyncio.Task] = set()


def schedule_request_post(rfq_id: str) -> None:
    """Post in the background so publishing never waits for Telegram."""
    task = asyncio.create_task(post_request_safely(rfq_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
