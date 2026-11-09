"""Posting new requests to a Telegram channel through the platform's bot.

The admin sets the bot token and the channel in the admin panel. Every public
request, once published, becomes one short post with a button that opens it on
the site. The token is stored encrypted and never sent back to the browser.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import re
from datetime import date, datetime
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.exceptions import ValidationError
from app.database import pool
from app.utils.audit import log_event

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,64}$")
CHANNEL_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{6,16}|-?\d{5,16})$")
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


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


async def call_bot(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    """One Bot API call. Tests replace this function; nothing else talks to Telegram."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.post(f"{API_BASE}/bot{token}/{method}", json=payload or {})
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


def _deadline(value: Any) -> str | None:
    if not value:
        return None
    try:
        day = value if isinstance(value, date) else datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None
    return f"{day.day} {MONTHS[day.month - 1]}"


def request_url(site_url: str, rfq_id: str) -> str:
    return f"{site_url.rstrip('/')}/supplier/rfqs/{rfq_id}"


def format_post(row, site_url: str) -> str:
    """Five short lines: what kind, the title, money and amount, place and term, the link."""
    kind = "Товары" if row["type"] == "product" else "Услуги"
    head = f"🆕 <b>Новая заявка</b> · {html.escape(row['category_name'] or kind)}"
    facts = [f"💰 {_budget(row)}"]
    if row["type"] == "product" and row["quantity"]:
        facts.append(f"📦 {row['quantity']} шт")
    if row["payment_mode"] == "direct":
        facts.append("⚡ оплата сразу")
    else:
        facts.append("🛡 с гарантией")
    place = row["delivery_city"] if row["type"] == "product" else None
    term = _deadline(row["deadline"])
    where_when = []
    if place:
        where_when.append(f"📍 {html.escape(place)}")
    if term:
        where_when.append(f"⏳ КП до {term}")
    lines = [head, f"<b>{html.escape(row['title'])}</b>", " · ".join(facts)]
    if where_when:
        lines.append(" · ".join(where_when))
    lines.append(f'<a href="{html.escape(request_url(site_url, row["id"]), quote=True)}">Открыть заявку →</a>')
    return "\n".join(lines)


POST_ROW_SQL = """
    SELECT r.id, r.title, r.type::text AS type, r.status::text AS status,
           r.visibility::text AS visibility, r.budget_type::text AS budget_type,
           r.budget_from, r.budget_to, r.currency, r.deadline, r.quantity,
           r.delivery_city, r.payment_mode,
           (
               SELECT c.name FROM categories c
               WHERE c.slug = r.category_id OR c.id::text = r.category_id
               LIMIT 1
           ) AS category_name
    FROM rfqs r
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

    payload = {
        "chat_id": channel,
        "text": format_post(row, site_url),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[{"text": "Открыть заявку", "url": request_url(site_url, rfq_id)}]]
        },
    }
    status, error, message_id = "sent", None, None
    try:
        result = await call_bot(token, "sendMessage", payload)
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
