import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

from app.config import get_settings
from app.core.exceptions import AppError, UnauthorizedError
from app.core.security_core import hash_password, verify_password
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.modules.auth.services.common import USER_COLUMNS, load_user
from app.utils.audit import AuditLogger

logger = logging.getLogger(__name__)

RESET_RESEND_COOLDOWN_SECONDS = 60
INVALID_TOKEN_MESSAGE = "Ссылка недействительна или устарела"
FORGOT_GENERIC_MESSAGE = "Если аккаунт существует, мы отправили письмо"


@dataclass
class EmailJob:
    to: str
    subject: str
    text: str
    html: str | None = None


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ttl_label(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours % 10 == 1 and hours % 100 != 11:
            return f"{hours} час"
        if hours % 10 in (2, 3, 4) and hours % 100 not in (12, 13, 14):
            return f"{hours} часа"
        return f"{hours} часов"
    if minutes % 10 == 1 and minutes % 100 != 11:
        return f"{minutes} минуту"
    if minutes % 10 in (2, 3, 4) and minutes % 100 not in (12, 13, 14):
        return f"{minutes} минуты"
    return f"{minutes} минут"


def build_reset_email(to: str, link: str, ttl_minutes: int) -> EmailJob:
    ttl = _ttl_label(ttl_minutes)
    text = (
        "Здравствуйте!\n\n"
        "Мы получили запрос на восстановление пароля. Чтобы задать новый пароль, "
        f"перейдите по ссылке:\n{link}\n\n"
        f"Ссылка действительна {ttl}.\n\n"
        "Если вы не запрашивали восстановление пароля, просто проигнорируйте это письмо."
    )
    safe_link = escape(link, quote=True)
    html = (
        "<p>Здравствуйте!</p>"
        "<p>Мы получили запрос на восстановление пароля. Чтобы задать новый пароль, "
        "нажмите на кнопку ниже:</p>"
        f'<p><a href="{safe_link}" style="display:inline-block;padding:10px 20px;'
        'background:#2563eb;color:#ffffff;text-decoration:none;border-radius:6px">'
        "Задать новый пароль</a></p>"
        f'<p>Или скопируйте ссылку в браузер:<br><a href="{safe_link}">{safe_link}</a></p>'
        f"<p>Ссылка действительна {ttl}.</p>"
        "<p>Если вы не запрашивали восстановление пароля, просто проигнорируйте это письмо.</p>"
    )
    return EmailJob(to=to, subject="Восстановление пароля", text=text, html=html)


def build_password_changed_email(to: str) -> EmailJob:
    text = (
        "Здравствуйте!\n\n"
        "Пароль вашего аккаунта был изменён.\n\n"
        "Если это сделали не вы, немедленно восстановите доступ через «Забыли пароль?» "
        "и свяжитесь с поддержкой."
    )
    html = (
        "<p>Здравствуйте!</p>"
        "<p>Пароль вашего аккаунта был изменён.</p>"
        "<p>Если это сделали не вы, немедленно восстановите доступ через "
        "«Забыли пароль?» и свяжитесь с поддержкой.</p>"
    )
    return EmailJob(to=to, subject="Пароль изменён", text=text, html=html)


class AuthPasswordMixin:
    async def request_password_reset(
        self, data: ForgotPasswordRequest, ip: str | None = None
    ) -> EmailJob | None:
        """Create a reset token. Returns the email to send, or None (nothing to send)."""
        settings = get_settings()
        email = data.email.lower()
        async with self.connection() as conn:
            async with conn.transaction():
                user = await conn.fetchrow(
                    "SELECT id, email, status::text AS status FROM users WHERE email = $1",
                    email,
                )
                if not user or user["status"] == "blocked":
                    return None
                recent = await conn.fetchval(
                    """
                    SELECT 1 FROM password_reset_tokens
                    WHERE user_id = $1
                      AND created_at > NOW() - make_interval(secs => $2)
                    LIMIT 1
                    """,
                    user["id"],
                    float(RESET_RESEND_COOLDOWN_SECONDS),
                )
                if recent:
                    return None
                await conn.execute(
                    """
                    UPDATE password_reset_tokens SET used_at = NOW()
                    WHERE user_id = $1 AND used_at IS NULL
                    """,
                    user["id"],
                )
                token = secrets.token_urlsafe(32)
                expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=settings.password_reset_ttl_minutes
                )
                await conn.execute(
                    """
                    INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, ip_address)
                    VALUES ($1, $2, $3, $4)
                    """,
                    user["id"],
                    hash_reset_token(token),
                    expires_at,
                    ip[:64] if ip else None,
                )
                await AuditLogger(conn).log_action(
                    user_id=user["id"],
                    actor_id=None,
                    company_id=None,
                    action="password_reset_requested",
                    resource_type="user",
                    resource_id=str(user["id"]),
                    ip_address=ip,
                )
        link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
        return build_reset_email(user["email"], link, settings.password_reset_ttl_minutes)

    async def reset_password(
        self, data: ResetPasswordRequest, ip: str | None = None
    ) -> EmailJob:
        async with self.connection() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id FROM password_reset_tokens
                    WHERE token_hash = $1
                      AND used_at IS NULL
                      AND expires_at > NOW()
                    FOR UPDATE
                    """,
                    hash_reset_token(data.token),
                )
                if not row:
                    raise AppError(400, INVALID_TOKEN_MESSAGE, "invalid_reset_token")
                user = await conn.fetchrow(
                    "SELECT id, email, status::text AS status FROM users WHERE id = $1",
                    row["user_id"],
                )
                if not user or user["status"] == "blocked":
                    raise AppError(400, INVALID_TOKEN_MESSAGE, "invalid_reset_token")
                await conn.execute(
                    """
                    UPDATE users
                    SET password_hash = $2,
                        token_version = COALESCE(token_version, 0) + 1,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    user["id"],
                    hash_password(data.new_password),
                )
                await conn.execute(
                    """
                    UPDATE password_reset_tokens SET used_at = NOW()
                    WHERE user_id = $1 AND used_at IS NULL
                    """,
                    user["id"],
                )
                await AuditLogger(conn).log_action(
                    user_id=user["id"],
                    actor_id=None,
                    company_id=None,
                    action="password_reset",
                    resource_type="user",
                    resource_id=str(user["id"]),
                    ip_address=ip,
                )
        return build_password_changed_email(user["email"])

    async def change_password(
        self,
        user_id: int,
        data: ChangePasswordRequest,
        side: str | None = None,
        ip: str | None = None,
    ) -> tuple[TokenResponse, EmailJob]:
        from app.modules.actors.services import user_capabilities

        async with self.connection() as conn:
            async with conn.transaction():
                user = await load_user(conn, user_id)
                if not user:
                    raise UnauthorizedError("User not found")
                if not verify_password(data.current_password, user["password_hash"]):
                    raise AppError(400, "Неверный текущий пароль", "invalid_current_password")
                if data.current_password == data.new_password:
                    raise AppError(
                        400, "Новый пароль должен отличаться от текущего", "same_password"
                    )
                row = await conn.fetchrow(
                    f"""
                    UPDATE users
                    SET password_hash = $2,
                        token_version = COALESCE(token_version, 0) + 1,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING {USER_COLUMNS}
                    """,
                    user_id,
                    hash_password(data.new_password),
                )
                await conn.execute(
                    """
                    UPDATE password_reset_tokens SET used_at = NOW()
                    WHERE user_id = $1 AND used_at IS NULL
                    """,
                    user_id,
                )
                await AuditLogger(conn).log_action(
                    user_id=user_id,
                    actor_id=None,
                    company_id=None,
                    action="password_changed",
                    resource_type="user",
                    resource_id=str(user_id),
                    ip_address=ip,
                )
        updated = dict(row)
        if side and not user_capabilities(updated["role"]).get(side):
            side = None
        return self._issue_tokens(updated, side=side), build_password_changed_email(
            updated["email"]
        )
