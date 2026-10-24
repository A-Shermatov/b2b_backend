"""TOTP helpers: encrypt secrets, verify codes, generate backup codes, render QR."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import secrets
import string
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.security_core import hash_password, verify_password

logger = logging.getLogger(__name__)

BACKUP_CODE_COUNT = 8
BACKUP_CODE_LENGTH = 8
TOTP_PERIOD = 30
VERIFY_WINDOW = 1
# Search ±10 minutes for clock-skew diagnosis (never logs the code/secret)
DIAGNOSE_OFFSET_RANGE = 20


def _fernet() -> Fernet:
    settings = get_settings()
    material = (settings.totp_encryption_key or settings.secret_key).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_totp_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_totp_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Invalid TOTP secret") from exc


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def _issuer_name() -> str:
    settings = get_settings()
    return (settings.totp_issuer or "BrendMarket").replace(" ", "")


def build_otpauth_url(secret: str, email: str) -> str:
    """Build a fully-specified otpauth URI (SHA1 / 6 digits / 30s)."""
    issuer = _issuer_name()
    label = f"{issuer}:{email.strip()}"
    query = urlencode(
        {
            "secret": secret.strip().replace(" ", ""),
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": "6",
            "period": "30",
        }
    )
    return f"otpauth://totp/{quote(label)}?{query}"


def render_totp_qr_data_url(otpauth_url: str) -> str:
    """PNG QR as data URL, generated from the exact otpauth URI stored for the user."""
    img = qrcode.make(otpauth_url, box_size=6, border=2)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _clean_totp_code(code: str) -> str:
    return "".join(ch for ch in str(code).strip() if ch.isdigit())


def verify_totp_code(secret: str, code: str) -> bool:
    cleaned = _clean_totp_code(code)
    if len(cleaned) != 6:
        return False
    # Standard ±1 step (±30s). Correct host/phone time is required.
    return bool(
        pyotp.TOTP(secret.strip()).verify(cleaned, valid_window=VERIFY_WINDOW)
    )


def diagnose_totp_mismatch(secret: str, code: str) -> dict[str, Any]:
    """Safe diagnostics for failed TOTP checks — never includes secret or code."""
    raw = str(code)
    cleaned = _clean_totp_code(code)
    now = time.time()
    period = int(now) // TOTP_PERIOD
    seconds_into_period = int(now) % TOTP_PERIOD
    info: dict[str, Any] = {
        "server_unix": int(now),
        "server_iso_utc": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "seconds_into_period": seconds_into_period,
        "verify_window": VERIFY_WINDOW,
        "raw_code_len": len(raw),
        "digit_code_len": len(cleaned),
        "code_all_digits": raw.strip().isdigit(),
        "secret_len": len(secret.strip()) if secret else 0,
        "match_offset": None,
        "reason": "unknown",
    }
    if not secret or not secret.strip():
        info["reason"] = "empty_secret"
        return info
    if len(cleaned) != 6:
        info["reason"] = "invalid_code_format"
        return info

    totp = pyotp.TOTP(secret.strip())
    if totp.verify(cleaned, valid_window=VERIFY_WINDOW):
        info["reason"] = "would_pass"
        info["match_offset"] = 0
        return info

    # Find nearest period where this code would be valid (clock skew / wrong secret)
    for offset in range(-DIAGNOSE_OFFSET_RANGE, DIAGNOSE_OFFSET_RANGE + 1):
        if totp.at(now + offset * TOTP_PERIOD) == cleaned:
            info["match_offset"] = offset
            skew_seconds = offset * TOTP_PERIOD
            info["estimated_clock_skew_seconds"] = skew_seconds
            if abs(offset) <= VERIFY_WINDOW:
                info["reason"] = "window_edge"
            else:
                info["reason"] = "clock_skew_or_stale_code"
            return info

    info["reason"] = "code_not_for_this_secret"
    return info


def log_totp_failure(context: str, *, user_id: int, secret: str, code: str) -> dict[str, Any]:
    info = diagnose_totp_mismatch(secret, code)
    logger.warning(
        "totp_verify_failed context=%s user_id=%s reason=%s "
        "match_offset=%s skew_s=%s server_unix=%s period=%s sec_in_period=%s "
        "digit_len=%s raw_len=%s all_digits=%s secret_len=%s window=%s",
        context,
        user_id,
        info.get("reason"),
        info.get("match_offset"),
        info.get("estimated_clock_skew_seconds"),
        info.get("server_unix"),
        info.get("period"),
        info.get("seconds_into_period"),
        info.get("digit_code_len"),
        info.get("raw_code_len"),
        info.get("code_all_digits"),
        info.get("secret_len"),
        info.get("verify_window"),
    )
    return info


def totp_failure_user_message(info: dict[str, Any]) -> str:
    """Client-facing 422 detail — never includes secret or code."""
    if info.get("reason") == "clock_skew_or_stale_code":
        return (
            "Код не совпал из‑за рассинхрона времени "
            "(телефон и сервер должны быть в пределах ±30 с). "
            "Синхронизируйте часы и введите свежий код."
        )
    if info.get("reason") == "invalid_code_format":
        return "Код должен состоять из 6 цифр"
    return "Invalid authentication code"


def generate_backup_codes() -> list[str]:
    alphabet = string.ascii_uppercase + string.digits
    codes: list[str] = []
    for _ in range(BACKUP_CODE_COUNT):
        codes.append("".join(secrets.choice(alphabet) for _ in range(BACKUP_CODE_LENGTH)))
    return codes


def hash_backup_codes(codes: list[str]) -> list[str]:
    return [hash_password(code.upper()) for code in codes]


def consume_backup_code(code: str, hashes: list[str]) -> list[str] | None:
    """Return remaining hashes if code matches one entry, else None."""
    normalized = code.strip().upper().replace(" ", "").replace("-", "")
    if not normalized:
        return None
    for idx, hashed in enumerate(hashes):
        if verify_password(normalized, hashed):
            return [h for i, h in enumerate(hashes) if i != idx]
    return None
