"""Who is who in the bot's private chats.

A person links their platform account to Telegram once: the site gives a
one-time link to the bot (t.me/<bot>?start=<code>), the person presses Start
there, and from then on the bot knows which account writes to it. One account
has one Telegram user and the other way round; linking again moves the link.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.core.exceptions import ValidationError
from app.database import pool
from app.modules.telegram.service import _load, _site_url, decrypt_token
from app.utils.audit import log_event

CODE_TTL = timedelta(minutes=15)
NOT_READY = "Бот в Telegram пока не подключён. Попробуйте позже."


async def assistant_state() -> dict:
    """What the bot needs to work in private chats, read once per use."""
    async with pool.connection() as conn:
        row = await _load(conn)
    token = decrypt_token(row.get("bot_token_enc"))
    return {
        "enabled": bool(row.get("assistant_enabled")) and bool(token) and bool(row.get("bot_username")),
        "token": token,
        "username": row.get("bot_username"),
        "site_url": _site_url(row),
        "update_offset": int(row.get("update_offset") or 0),
    }


def _account_view(state: dict, account) -> dict:
    return {
        "available": state["enabled"],
        "bot_username": state["username"] if state["enabled"] else None,
        "linked": account is not None,
        "telegram_username": account["username"] if account else None,
        "telegram_name": account["first_name"] if account else None,
        "linked_at": account["linked_at"] if account else None,
    }


async def link_status(user_id: int) -> dict:
    state = await assistant_state()
    async with pool.connection() as conn:
        account = await conn.fetchrow(
            "SELECT username, first_name, linked_at FROM telegram_accounts WHERE user_id = $1", user_id
        )
    return _account_view(state, account)


async def create_link(user_id: int) -> dict:
    """A fresh one-time link to the bot; older unused ones of the person stop working."""
    state = await assistant_state()
    if not state["enabled"]:
        raise ValidationError(NOT_READY)
    code = secrets.token_urlsafe(18)
    expires_at = datetime.now(timezone.utc) + CODE_TTL
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM telegram_link_codes WHERE user_id = $1 OR expires_at < NOW() - INTERVAL '1 day'",
                user_id,
            )
            await conn.execute(
                "INSERT INTO telegram_link_codes (code, user_id, expires_at) VALUES ($1, $2, $3)",
                code,
                user_id,
                expires_at,
            )
    return {"url": f"https://t.me/{state['username']}?start={code}", "expires_at": expires_at}


async def unlink(user_id: int) -> dict:
    async with pool.connection() as conn:
        removed = await conn.fetchval(
            "DELETE FROM telegram_accounts WHERE user_id = $1 RETURNING telegram_id", user_id
        )
        if removed is not None:
            await log_event(conn, "telegram.account.unlink", "user", user_id, user_id=user_id)
    return await link_status(user_id)


async def redeem(code: str, telegram_user: dict, chat_id: int) -> dict | None:
    """Use a link code that came with /start: the account's row, or None for a bad code."""
    telegram_id = int(telegram_user["id"])
    async with pool.connection() as conn:
        async with conn.transaction():
            user_id = await conn.fetchval(
                """
                UPDATE telegram_link_codes SET used_at = NOW()
                WHERE code = $1 AND used_at IS NULL AND expires_at > NOW()
                RETURNING user_id
                """,
                code,
            )
            if user_id is None:
                return None
            # One Telegram user per account and one account per Telegram user.
            await conn.execute(
                "DELETE FROM telegram_accounts WHERE user_id = $1 OR telegram_id = $2", user_id, telegram_id
            )
            await conn.execute(
                """
                INSERT INTO telegram_accounts (user_id, telegram_id, chat_id, username, first_name)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id,
                telegram_id,
                chat_id,
                (telegram_user.get("username") or None),
                (telegram_user.get("first_name") or "")[:128] or None,
            )
            await log_event(
                conn,
                "telegram.account.link",
                "user",
                user_id,
                user_id=user_id,
                details={"telegram_id": telegram_id, "username": telegram_user.get("username")},
            )
            row = await conn.fetchrow(
                "SELECT id, first_name, last_name, role::text AS role FROM users WHERE id = $1", user_id
            )
    return dict(row) if row else None


async def user_of_telegram(telegram_id: int) -> int | None:
    async with pool.connection() as conn:
        return await conn.fetchval(
            "SELECT user_id FROM telegram_accounts WHERE telegram_id = $1", int(telegram_id)
        )


async def chat_of_user(user_id: int) -> int | None:
    async with pool.connection() as conn:
        return await conn.fetchval("SELECT chat_id FROM telegram_accounts WHERE user_id = $1", user_id)
