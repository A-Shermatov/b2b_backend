import hashlib
from datetime import datetime, timezone

import asyncpg

from app.config import get_settings
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    RateLimitError,
    UnauthorizedError,
    ValidationError,
)
from app.core.security_core import (
    create_2fa_challenge_token,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.actors.services import ActorService, user_capabilities
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResult,
    RegisterRequest,
    TokenResponse,
    TwoFactorVerifyRequest,
)
from app.modules.auth.services.common import (
    USER_COLUMNS,
    block_message,
    lift_expired_ban,
    load_user,
)
from app.utils.audit import AuditLogger, log_event
from app.utils import redis_client
from app.utils.redis_client import (
    blacklist_refresh_token,
    is_refresh_token_blacklisted,
    mark_refresh_rotated,
    refresh_rotation_in_grace,
)
from app.utils.totp import (
    consume_backup_code,
    decrypt_totp_secret,
    log_totp_failure,
    totp_failure_user_message,
    verify_totp_code,
)


_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")
REFRESH_REUSE_GRACE_SECONDS = 30


class AuthCreateMixin:
    async def register(self, data: RegisterRequest, ip: str | None = None) -> TokenResponse:
        from app.modules.companies.schemas import CompanyWizardInput
        from app.modules.companies.services import CompanyService
        from app.modules.companies.services.invitations import (
            GONE_MESSAGE,
            OTHER_EMAIL_MESSAGE,
            hash_invite_token,
        )

        company_title = (data.company_title or "").strip()
        as_company = data.account_type == "company" and not data.invite_token
        if as_company and len(company_title) < 2:
            raise ValidationError("Укажите название компании")
        invitation = None
        inviter_note = None
        async with self.connection() as conn:
            async with conn.transaction():
                if data.invite_token:
                    invitation = await conn.fetchrow(
                        """
                        SELECT i.id, i.company_id, i.email, i.role, i.status, i.invited_by,
                               i.expires_at, c.title AS company_title
                        FROM company_invitations i
                        JOIN companies c ON c.id = i.company_id
                        WHERE i.token_hash = $1
                        FOR UPDATE OF i
                        """,
                        hash_invite_token(data.invite_token),
                    )
                    if (
                        not invitation
                        or invitation["status"] != "pending"
                        or invitation["expires_at"] <= datetime.now(timezone.utc)
                    ):
                        raise ConflictError(GONE_MESSAGE)
                    if invitation["email"].lower() != data.email.lower():
                        raise ForbiddenError(OTHER_EMAIL_MESSAGE, code="invitation_other_email")
                existing = await conn.fetchrow(
                    "SELECT id FROM users WHERE email = $1",
                    data.email.lower(),
                )
                if existing:
                    raise ConflictError("Email already registered")
                try:
                    user = await conn.fetchrow(
                    f"""
                    INSERT INTO users (
                        email, password_hash, first_name, last_name, phone,
                        role, role_id, status, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6::user_role,
                        (SELECT id FROM roles WHERE name = $6::text),
                        'active', NOW(), NOW()
                    )
                    RETURNING {USER_COLUMNS}
                    """,
                    data.email.lower(),
                    hash_password(data.password),
                    data.first_name,
                    data.last_name,
                    data.phone,
                    data.role,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise ConflictError("Email already registered") from exc
                user_dict = dict(user)
                await ActorService(conn).ensure_individual_actors_for_user(user_dict)
                # Listed among suppliers as a private person only when registering as one.
                private_supplier = (
                    data.account_type == "individual"
                    and not invitation
                    and data.role in ("supplier", "both")
                )
                await conn.execute(
                    """
                    INSERT INTO individual_profiles (
                        user_id, headline, accepts_orders, prefers_company
                    )
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    user_dict["id"],
                    (data.headline or "").strip() or None if private_supplier else None,
                    private_supplier,
                    bool(invitation) or as_company,
                )
                companies = CompanyService(conn)
                if invitation:
                    await companies.join_by_invitation(conn, dict(invitation), user_dict)
                    inviter_note = dict(invitation)
                elif as_company:
                    sides = ["buyer", "supplier"] if data.role == "both" else [data.role]
                    await companies.create_from_wizard(
                        user_dict,
                        CompanyWizardInput(title=company_title, actor_types=sides),
                    )
                if invitation:
                    # join_by_invitation may have widened the role to the company's sides.
                    user_dict = dict(
                        await conn.fetchrow(
                            f"SELECT {USER_COLUMNS} FROM users WHERE id = $1", user_dict["id"]
                        )
                    )
                await AuditLogger(conn).log_action(
                    user_id=user_dict["id"],
                    actor_id=None,
                    company_id=None,
                    action="register",
                    resource_type="user",
                    resource_id=str(user_dict["id"]),
                    ip_address=ip,
                    details={
                        "account_type": "invited" if invitation else data.account_type,
                    },
                )
        if inviter_note:
            await CompanyService()._tell_inviter(inviter_note, user_dict, accepted=True)
        return self._issue_tokens(user_dict)

    async def login(self, data: LoginRequest, ip: str | None = None) -> LoginResult:
        async with self.connection() as conn:
            user = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM users WHERE email = $1",
                data.email.lower(),
            )
            if not user:
                # Same bcrypt cost as for a real account: response time must not
                # reveal whether the email is registered.
                verify_password(data.password, _DUMMY_PASSWORD_HASH)
            if not user or not verify_password(data.password, user["password_hash"]):
                if user:
                    await AuditLogger(conn).log_action(
                        user_id=user["id"],
                        actor_id=None,
                        company_id=None,
                        action="login_failed",
                        resource_type="user",
                        resource_id=str(user["id"]),
                        ip_address=ip,
                    )
                else:
                    await AuditLogger(conn).log_action(
                        user_id=None,
                        actor_id=None,
                        company_id=None,
                        action="login_failed",
                        resource_type="user",
                        resource_id=data.email.lower()[:100],
                        ip_address=ip,
                    )
                raise UnauthorizedError("Invalid email or password")
            user_dict = dict(user)
            user_dict = await lift_expired_ban(conn, user_dict)
            if user_dict["status"] == "blocked":
                raise UnauthorizedError(block_message(user_dict))
            if data.role and not user_capabilities(user_dict["role"]).get(data.role):
                raise ForbiddenError("Account cannot sign in with this role")
            if user_dict.get("totp_enabled") and user_dict.get("totp_secret"):
                extra = {"side": data.role} if data.role else None
                challenge = create_2fa_challenge_token(str(user_dict["id"]), extra)
                await AuditLogger(conn).log_action(
                    user_id=user_dict["id"],
                    actor_id=None,
                    company_id=None,
                    action="login_2fa_challenge",
                    resource_type="user",
                    resource_id=str(user_dict["id"]),
                    ip_address=ip,
                )
                return LoginResult(
                    requires_2fa=True,
                    challenge_token=challenge,
                    role=data.role,
                )
            await AuditLogger(conn).log_action(
                user_id=user_dict["id"],
                actor_id=None,
                company_id=None,
                action="login",
                resource_type="user",
                resource_id=str(user_dict["id"]),
                ip_address=ip,
            )
        tokens = self._issue_tokens(user_dict, side=data.role)
        return LoginResult(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_type=tokens.token_type,
            role=tokens.role,
        )

    async def verify_2fa(
        self, data: TwoFactorVerifyRequest, ip: str | None = None
    ) -> TokenResponse:
        try:
            payload = decode_token(data.challenge_token)
        except ValueError as exc:
            raise UnauthorizedError("Invalid or expired 2FA challenge") from exc
        if payload.get("type") != "2fa_challenge":
            raise UnauthorizedError("Invalid 2FA challenge")
        user_id = int(payload["sub"])
        side = payload.get("side")
        # Per account, not per IP: the 6-digit code must not be brute-forced.
        settings = get_settings()
        if not await redis_client.check_rate_limit(
            f"2fa_user:{user_id}",
            settings.rate_limit_2fa_per_user,
            settings.rate_limit_2fa_user_window_seconds,
        ):
            raise RateLimitError("Too many 2FA attempts. Try again later.")
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user or not user.get("totp_enabled") or not user.get("totp_secret"):
                raise UnauthorizedError("2FA is not enabled for this account")
            user = await lift_expired_ban(conn, user)
            if user["status"] == "blocked":
                raise UnauthorizedError(block_message(user))
            try:
                secret = decrypt_totp_secret(user["totp_secret"])
            except ValueError as exc:
                raise UnauthorizedError("Invalid 2FA configuration") from exc
            code_ok = verify_totp_code(secret, data.code)
            if code_ok:
                # A code stays valid for up to 90 s (±1 step): accept it only once, so
                # an intercepted or shoulder-surfed code cannot be replayed.
                digits = "".join(ch for ch in str(data.code) if ch.isdigit())
                if not await redis_client.claim_once(f"totp_used:{user_id}:{digits}", 120):
                    from app.core.exceptions import ValidationError

                    raise ValidationError("Этот код уже использован. Дождитесь следующего кода")
            remaining_hashes = None
            if not code_ok:
                hashes = list(user.get("totp_backup_codes_hash") or [])
                remaining_hashes = consume_backup_code(data.code, hashes)
                if remaining_hashes is None:
                    from app.core.exceptions import ValidationError

                    info = log_totp_failure(
                        "login_verify",
                        user_id=user_id,
                        secret=secret,
                        code=data.code,
                    )
                    # 422 (not 401): wrong OTP must not trigger client session-refresh retry
                    raise ValidationError(totp_failure_user_message(info))
                await conn.execute(
                    """
                    UPDATE users
                    SET totp_backup_codes_hash = $2::text[], updated_at = NOW()
                    WHERE id = $1
                    """,
                    user_id,
                    remaining_hashes,
                )
            await AuditLogger(conn).log_action(
                user_id=user_id,
                actor_id=None,
                company_id=None,
                action="login",
                resource_type="user",
                resource_id=str(user_id),
                ip_address=ip,
                details={"via": "2fa_backup" if remaining_hashes is not None else "2fa_totp"},
            )
        if side and not user_capabilities(user["role"]).get(side):
            side = None
        return self._issue_tokens(user, side=side)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_token(refresh_token)
        except ValueError as exc:
            raise UnauthorizedError("Invalid refresh token") from exc
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Invalid token type")
        jti_hash = hashlib.sha256(payload["jti"].encode()).hexdigest()
        user_id = int(payload["sub"])
        side = payload.get("side")
        async with self.connection() as conn:
            # Revocation lives in the DB (survives Redis restarts and is shared by all
            # workers); Redis is only a fast path.
            revoked = await is_refresh_token_blacklisted(jti_hash) or bool(
                await conn.fetchval(
                    "SELECT 1 FROM refresh_token_blacklist WHERE token_jti_hash = $1",
                    jti_hash,
                )
            )
            # A just-rotated token may be presented again by a parallel tab for a few
            # seconds; any later reuse is refused.
            if revoked and not await refresh_rotation_in_grace(jti_hash):
                raise UnauthorizedError("Token revoked")
            user = await load_user(conn, user_id)
            if not user:
                raise UnauthorizedError("User not found")
            user = await lift_expired_ban(conn, user)
            if user["status"] == "blocked":
                raise UnauthorizedError(block_message(user))
            if int(payload.get("tv") or 0) != int(user.get("token_version") or 0):
                raise UnauthorizedError("Session revoked")
            if not revoked:
                # Rotation: every refresh token works once.
                expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
                await conn.execute(
                    """
                    INSERT INTO refresh_token_blacklist (token_jti_hash, expires_at)
                    VALUES ($1, $2)
                    ON CONFLICT (token_jti_hash) DO NOTHING
                    """,
                    jti_hash,
                    expires_at,
                )
                await blacklist_refresh_token(jti_hash, expires_at)
                await mark_refresh_rotated(jti_hash, REFRESH_REUSE_GRACE_SECONDS)
        if side and not user_capabilities(user["role"]).get(side):
            side = None
        return self._issue_tokens(user, side=side)

    async def logout(self, refresh_token: str) -> None:
        try:
            payload = decode_token(refresh_token)
        except ValueError:
            return
        jti = payload.get("jti")
        if not jti:
            return
        jti_hash = hashlib.sha256(jti.encode()).hexdigest()
        expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT INTO refresh_token_blacklist (token_jti_hash, expires_at)
                VALUES ($1, $2)
                ON CONFLICT (token_jti_hash) DO NOTHING
                """,
                jti_hash,
                expires_at,
            )
            try:
                logout_user_id = int(payload.get("sub"))
            except (TypeError, ValueError):
                logout_user_id = None
            if logout_user_id is not None:
                await log_event(
                    conn, "auth.logout", "user", logout_user_id, user_id=logout_user_id
                )
        await blacklist_refresh_token(jti_hash, expires_at)

    def _issue_tokens(self, user: dict, side: str | None = None) -> TokenResponse:
        extra = {"role": user["role"], "tv": int(user.get("token_version") or 0)}
        if side:
            extra["side"] = side
        access = create_access_token(str(user["id"]), extra)
        refresh_extra = {"tv": int(user.get("token_version") or 0)}
        if side:
            refresh_extra["side"] = side
        refresh, _ = create_refresh_token(str(user["id"]), refresh_extra)
        return TokenResponse(access_token=access, refresh_token=refresh, role=side)
