import hashlib
import re
import uuid

import pytest

from app.config import Settings
from app.database import pool
from app.utils import email as email_utils

API = "/api/v1"
GENERIC = "Если аккаунт существует, мы отправили письмо"


@pytest.fixture
def sent_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return True

    monkeypatch.setattr(email_utils, "send_email", _fake_send)
    return sent


async def _register(client, email: str, password: str = "Password1!") -> dict:
    res = await client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Reset",
            "last_name": "User",
            "role": "buyer",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _login(client, email: str, password: str):
    return await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password, "role": "buyer"},
    )


def _token_from(sent: list[dict]) -> str:
    match = re.search(r"/reset-password\?token=([\w-]+)", sent[-1]["text"])
    assert match, sent[-1]["text"]
    return match.group(1)


async def _forgot(client, email: str):
    return await client.post(f"{API}/auth/password/forgot", json={"email": email})


@pytest.mark.asyncio
async def test_forgot_unknown_email_is_generic(client, sent_emails):
    res = await _forgot(client, f"nobody-{uuid.uuid4().hex[:8]}@example.com")
    assert res.status_code == 200, res.text
    assert res.json() == {"detail": GENERIC}
    assert sent_emails == []


@pytest.mark.asyncio
async def test_forgot_existing_creates_token_and_sends_email(client, sent_emails):
    email = f"pw-forgot-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, email)
    res = await _forgot(client, email)
    assert res.status_code == 200, res.text
    assert res.json() == {"detail": GENERIC}
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == email
    assert sent_emails[0]["subject"] == "Восстановление пароля"
    token = _token_from(sent_emails)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT t.token_hash, t.used_at FROM password_reset_tokens t
            JOIN users u ON u.id = t.user_id WHERE u.email = $1
            """,
            email,
        )
    assert row is not None
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["token_hash"] != token
    assert row["used_at"] is None

    # second request within cooldown does not send another email
    again = await _forgot(client, email)
    assert again.status_code == 200
    assert len(sent_emails) == 1


@pytest.mark.asyncio
async def test_reset_password_flow(client, sent_emails):
    email = f"pw-reset-{uuid.uuid4().hex[:8]}@example.com"
    old, new = "Password1!", "NewPassword2!"
    tokens = await _register(client, email, old)
    await _forgot(client, email)
    token = _token_from(sent_emails)

    res = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": new}
    )
    assert res.status_code == 200, res.text
    assert "detail" in res.json()
    # confirmation email "Пароль изменён"
    assert sent_emails[-1]["subject"] == "Пароль изменён"

    assert (await _login(client, email, old)).status_code == 401
    assert (await _login(client, email, new)).status_code == 200

    # old sessions are revoked
    me = await client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 401
    refreshed = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 401

    # token cannot be reused
    reuse = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": "Another3!pass"}
    )
    assert reuse.status_code == 400
    assert reuse.json()["detail"] == "Ссылка недействительна или устарела"


@pytest.mark.asyncio
async def test_reset_invalid_and_weak_password(client, sent_emails):
    bad = await client.post(
        f"{API}/auth/password/reset",
        json={"token": "does-not-exist", "new_password": "NewPassword2!"},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Ссылка недействительна или устарела"

    email = f"pw-weak-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, email)
    await _forgot(client, email)
    weak = await client.post(
        f"{API}/auth/password/reset",
        json={"token": _token_from(sent_emails), "new_password": "short"},
    )
    assert weak.status_code == 422


@pytest.mark.asyncio
async def test_reset_expired_token_fails(client, sent_emails):
    email = f"pw-exp-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, email)
    await _forgot(client, email)
    token = _token_from(sent_emails)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE password_reset_tokens SET expires_at = NOW() - INTERVAL '1 minute' "
            "WHERE token_hash = $1",
            hashlib.sha256(token.encode()).hexdigest(),
        )
    res = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": "NewPassword2!"}
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Ссылка недействительна или устарела"


@pytest.mark.asyncio
async def test_new_forgot_invalidates_previous_token(client, sent_emails):
    email = f"pw-inv-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, email)
    await _forgot(client, email)
    first = _token_from(sent_emails)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE password_reset_tokens SET created_at = NOW() - INTERVAL '5 minutes'
            WHERE user_id = (SELECT id FROM users WHERE email = $1)
            """,
            email,
        )
    await _forgot(client, email)
    second = _token_from(sent_emails)
    assert first != second
    res = await client.post(
        f"{API}/auth/password/reset", json={"token": first, "new_password": "NewPassword2!"}
    )
    assert res.status_code == 400
    res = await client.post(
        f"{API}/auth/password/reset", json={"token": second, "new_password": "NewPassword2!"}
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_blocked_user_gets_no_email(client, sent_emails):
    email = f"pw-blk-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, email)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET status = 'blocked'::user_status WHERE email = $1", email
        )
    res = await _forgot(client, email)
    assert res.status_code == 200
    assert res.json() == {"detail": GENERIC}
    assert sent_emails == []


@pytest.mark.asyncio
async def test_change_password(client, sent_emails):
    email = f"pw-change-{uuid.uuid4().hex[:8]}@example.com"
    old, new = "Password1!", "NewPassword2!"
    tokens = await _register(client, email, old)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    wrong = await client.post(
        f"{API}/auth/password/change",
        headers=headers,
        json={"current_password": "WrongPass1!", "new_password": new},
    )
    assert wrong.status_code == 400

    anon = await client.post(
        f"{API}/auth/password/change",
        json={"current_password": old, "new_password": new},
    )
    assert anon.status_code == 401

    res = await client.post(
        f"{API}/auth/password/change",
        headers=headers,
        json={"current_password": old, "new_password": new},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"] and body["refresh_token"]
    assert sent_emails[-1]["subject"] == "Пароль изменён"
    assert sent_emails[-1]["to"] == email

    # old access token rejected (token_version bump), new one works
    old_me = await client.get(f"{API}/auth/me", headers=headers)
    assert old_me.status_code == 401
    new_me = await client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert new_me.status_code == 200

    assert (await _login(client, email, old)).status_code == 401
    assert (await _login(client, email, new)).status_code == 200


@pytest.mark.asyncio
async def test_send_email_disabled_returns_false(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql://x@localhost/x_test")
    assert settings.smtp_enabled is False
    monkeypatch.setattr(email_utils, "get_settings", lambda: settings)
    assert await email_utils.send_email("a@example.com", "Subj", "Body") is False


@pytest.mark.asyncio
async def test_send_email_smtp_error_returns_false(monkeypatch):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://x@localhost/x_test",
        smtp_host="127.0.0.1",
        smtp_port=1,
        smtp_from_email="noreply@example.com",
        smtp_timeout=1,
    )
    assert settings.smtp_enabled is True
    monkeypatch.setattr(email_utils, "get_settings", lambda: settings)
    assert await email_utils.send_email("a@example.com", "Subj", "Body", "<p>Body</p>") is False
