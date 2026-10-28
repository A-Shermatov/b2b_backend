import uuid

import pyotp
import pytest

API = "/api/v1"


async def _register(client, email: str, password: str = "Password1!") -> dict:
    res = await client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Two",
            "last_name": "Factor",
            "role": "buyer",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_2fa_setup_confirm_login_challenge(client):
    email = f"totp-user-{uuid.uuid4().hex[:8]}@example.com"
    password = "Password1!"
    tokens = await _register(client, email, password)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    setup = await client.post(f"{API}/auth/2fa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert body["secret"]
    assert body["otpauth_url"].startswith("otpauth://")
    assert "algorithm=SHA1" in body["otpauth_url"]
    assert "digits=6" in body["otpauth_url"]
    assert "period=30" in body["otpauth_url"]
    assert body["qr_data_url"].startswith("data:image/png;base64,")
    assert len(body["backup_codes"]) == 8

    code = pyotp.TOTP(body["secret"]).now()
    confirm = await client.post(
        f"{API}/auth/2fa/confirm",
        headers=headers,
        json={"code": code},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["totp_enabled"] is True

    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password, "role": "buyer"},
    )
    assert login.status_code == 200, login.text
    challenge = login.json()
    assert challenge["requires_2fa"] is True
    assert challenge["challenge_token"]
    assert challenge.get("access_token") in (None, "")

    verify_code = pyotp.TOTP(body["secret"]).now()
    verify = await client.post(
        f"{API}/auth/2fa/verify",
        json={"challenge_token": challenge["challenge_token"], "code": verify_code},
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["access_token"]
    assert verify.json()["refresh_token"]


@pytest.mark.asyncio
async def test_2fa_backup_code_and_disable(client):
    email = f"totp-backup-{uuid.uuid4().hex[:8]}@example.com"
    password = "Password1!"
    tokens = await _register(client, email, password)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    setup = await client.post(f"{API}/auth/2fa/setup", headers=headers)
    secret = setup.json()["secret"]
    backup = setup.json()["backup_codes"][0]
    code = pyotp.TOTP(secret).now()
    await client.post(f"{API}/auth/2fa/confirm", headers=headers, json={"code": code})

    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password, "role": "buyer"},
    )
    challenge = login.json()["challenge_token"]
    verify = await client.post(
        f"{API}/auth/2fa/verify",
        json={"challenge_token": challenge, "code": backup},
    )
    assert verify.status_code == 200, verify.text
    headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}

    disable_code = pyotp.TOTP(secret).now()
    disable = await client.post(
        f"{API}/auth/2fa/disable",
        headers=headers,
        json={"password": password, "code": disable_code},
    )
    assert disable.status_code == 200, disable.text
    assert disable.json()["totp_enabled"] is False

    login2 = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password, "role": "buyer"},
    )
    assert login2.status_code == 200, login2.text
    assert login2.json().get("requires_2fa") is False
    assert login2.json()["access_token"]
