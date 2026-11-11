import logging

from app.core.exceptions import ConflictError, UnauthorizedError, ValidationError
from app.core.security_core import verify_password
from app.modules.actors.services import ActorService
from app.modules.auth.schemas import (
    ActivateRoleRequest,
    MeResponse,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
    TwoFactorSetupResponse,
    UserPublic,
    UserUpdateRequest,
)
from app.modules.auth.services.common import USER_COLUMNS, load_user, shape_user_public
from app.utils.audit import log_event
from app.utils.totp import (
    build_otpauth_url,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_codes,
    log_totp_failure,
    render_totp_qr_data_url,
    totp_failure_user_message,
    verify_totp_code,
)

logger = logging.getLogger(__name__)


class AuthUpdateMixin:
    async def update_me(self, user_id: int, data: UserUpdateRequest) -> UserPublic:
        """The phone changes at once; the name others see waits for an admin."""
        from app.modules.companies.services.changes import queue_user_changes

        async with self.connection() as conn:
            async with conn.transaction():
                user = await load_user(conn, user_id)
                if not user:
                    raise UnauthorizedError("User not found")
                if data.phone is not None and data.phone != user["phone"]:
                    await conn.execute(
                        "UPDATE users SET phone = $1, updated_at = NOW() WHERE id = $2",
                        data.phone,
                        user_id,
                    )
                name = {
                    field: value.strip()
                    for field in ("first_name", "last_name")
                    if (value := getattr(data, field)) is not None and value.strip()
                }
                await queue_user_changes(conn, user_id, name)
                return await self._user_with_pending(conn, user_id)

    async def set_avatar(self, user_id: int, key: str | None) -> UserPublic:
        """A new photo waits for an admin; removing the photo happens at once."""
        from app.modules.companies.services.changes import queue_user_changes

        async with self.connection() as conn:
            async with conn.transaction():
                if key:
                    await queue_user_changes(conn, user_id, {"avatar_url": key})
                else:
                    row = await conn.fetchrow(
                        "UPDATE users SET avatar_url = NULL, updated_at = NOW() WHERE id = $1 RETURNING id",
                        user_id,
                    )
                    if not row:
                        raise UnauthorizedError("User not found")
                    # A photo still waiting for review goes away with it.
                    await queue_user_changes(conn, user_id, {"avatar_url": None})
                await log_event(
                    conn,
                    "user.avatar.submit" if key else "user.avatar.remove",
                    "user",
                    user_id,
                    user_id=user_id,
                )
                return await self._user_with_pending(conn, user_id)

    async def _user_with_pending(self, conn, user_id: int) -> UserPublic:
        from app.modules.companies.services.changes import pending_user_change_request

        row = await conn.fetchrow(f"SELECT {USER_COLUMNS} FROM users WHERE id = $1", user_id)
        if not row:
            raise UnauthorizedError("User not found")
        user = shape_user_public(dict(row))
        pending = await pending_user_change_request(conn, user_id)
        user.pending_changes = sorted(pending["proposed"]) if pending else []
        return user

    async def my_pending_changes(self, user_id: int) -> dict | None:
        from app.modules.companies.services.changes import pending_user_change_request

        async with self.connection() as conn:
            return await pending_user_change_request(conn, user_id)

    async def switch_actor(self, user_id: int, actor_id: int) -> MeResponse:
        actor_svc = ActorService(self.conn)
        actor = await actor_svc.get_by_id(actor_id)
        if not actor or not await actor_svc.user_owns_actor(user_id, actor):
            raise ValidationError("Actor not linked to user")
        async with self.connection() as conn:
            await log_event(
                conn,
                "auth.switch_actor",
                "user",
                user_id,
                user_id=user_id,
                actor_id=actor_id,
                details={"actor_id": actor_id},
            )
        return await self.get_me(user_id, active_actor_id=actor_id)

    async def switch_company(self, user_id: int, company_id: int) -> MeResponse:
        async with self.connection() as conn:
            membership = await conn.fetchrow(
                """
                SELECT id FROM company_users
                WHERE user_id = $1 AND company_id = $2
                """,
                user_id,
                company_id,
            )
            if not membership:
                raise ValidationError("Company not linked to user")
            company = await conn.fetchrow(
                "SELECT id, actor_type::text AS actor_type FROM companies WHERE id = $1",
                company_id,
            )
        if not company:
            raise ValidationError("Company not found")
        actor = await ActorService(self.conn).get_company_actor(
            company_id, company["actor_type"]
        )
        if not actor:
            raise ValidationError("Company actor not found")
        async with self.connection() as conn:
            await log_event(
                conn,
                "auth.switch_company",
                "user",
                user_id,
                user_id=user_id,
                actor_id=actor["id"],
                company_id=company_id,
                details={"company_id": company_id, "actor_id": actor["id"]},
            )
        return await self.get_me(user_id, active_actor_id=actor["id"])

    async def setup_2fa(self, user_id: int) -> TwoFactorSetupResponse:
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                raise UnauthorizedError("User not found")
            if user.get("totp_enabled"):
                raise ConflictError("2FA is already enabled")
            # Reuse pending secret so a repeated setup / double-click does not
            # invalidate a QR the user already scanned.
            reused_pending = False
            if user.get("totp_secret"):
                try:
                    secret = decrypt_totp_secret(user["totp_secret"])
                    reused_pending = True
                except ValueError:
                    logger.warning(
                        "totp_setup pending_secret_decrypt_failed user_id=%s",
                        user_id,
                    )
                    secret = generate_totp_secret()
            else:
                secret = generate_totp_secret()
            backup_codes = generate_backup_codes()
            await conn.execute(
                """
                UPDATE users
                SET totp_secret = $2,
                    totp_enabled = false,
                    totp_backup_codes_hash = $3::text[],
                    updated_at = NOW()
                WHERE id = $1
                """,
                user_id,
                encrypt_totp_secret(secret),
                hash_backup_codes(backup_codes),
            )
        otpauth_url = build_otpauth_url(secret, user["email"])
        qr_data_url = render_totp_qr_data_url(otpauth_url)
        logger.info(
            "totp_setup user_id=%s reused_pending=%s secret_len=%s "
            "otpauth_has_alg=%s qr_bytes≈%s",
            user_id,
            reused_pending,
            len(secret),
            "algorithm=SHA1" in otpauth_url,
            max(0, len(qr_data_url) - len("data:image/png;base64,")),
        )
        return TwoFactorSetupResponse(
            otpauth_url=otpauth_url,
            secret=secret,
            backup_codes=backup_codes,
            qr_data_url=qr_data_url,
        )

    async def confirm_2fa(self, user_id: int, data: TwoFactorConfirmRequest) -> UserPublic:
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                raise UnauthorizedError("User not found")
            if user.get("totp_enabled"):
                logger.warning("totp_confirm user_id=%s reason=already_enabled", user_id)
                raise ConflictError("2FA is already enabled")
            if not user.get("totp_secret"):
                logger.warning("totp_confirm user_id=%s reason=no_pending_secret", user_id)
                raise ValidationError("Start 2FA setup first")
            try:
                secret = decrypt_totp_secret(user["totp_secret"])
            except ValueError as exc:
                logger.warning("totp_confirm user_id=%s reason=decrypt_failed", user_id)
                raise ValidationError("Invalid 2FA setup state") from exc
            if not verify_totp_code(secret, data.code):
                info = log_totp_failure(
                    "confirm", user_id=user_id, secret=secret, code=data.code
                )
                # 422 (not 401): clients must not treat a wrong OTP as an expired session
                raise ValidationError(totp_failure_user_message(info))
            row = await conn.fetchrow(
                f"""
                UPDATE users
                SET totp_enabled = true, updated_at = NOW()
                WHERE id = $1
                RETURNING {USER_COLUMNS}
                """,
                user_id,
            )
            await log_event(conn, "auth.2fa.enable", "user", user_id, user_id=user_id)
        logger.info("totp_confirm_ok user_id=%s", user_id)
        return shape_user_public(dict(row))

    async def disable_2fa(self, user_id: int, data: TwoFactorDisableRequest) -> UserPublic:
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                raise UnauthorizedError("User not found")
            if not user.get("totp_enabled") or not user.get("totp_secret"):
                raise ValidationError("2FA is not enabled")
            if not verify_password(data.password, user["password_hash"]):
                raise UnauthorizedError("Invalid password")
            try:
                secret = decrypt_totp_secret(user["totp_secret"])
            except ValueError as exc:
                raise ValidationError("Invalid 2FA configuration") from exc
            if not verify_totp_code(secret, data.code):
                from app.utils.totp import consume_backup_code

                remaining = consume_backup_code(
                    data.code, list(user.get("totp_backup_codes_hash") or [])
                )
                if remaining is None:
                    info = log_totp_failure(
                        "disable", user_id=user_id, secret=secret, code=data.code
                    )
                    raise ValidationError(totp_failure_user_message(info))
            row = await conn.fetchrow(
                f"""
                UPDATE users
                SET totp_secret = NULL,
                    totp_enabled = false,
                    totp_backup_codes_hash = '{{}}'::text[],
                    updated_at = NOW()
                WHERE id = $1
                RETURNING {USER_COLUMNS}
                """,
                user_id,
            )
            await log_event(conn, "auth.2fa.disable", "user", user_id, user_id=user_id)
        logger.info("totp_disable_ok user_id=%s", user_id)
        return shape_user_public(dict(row))

    async def activate_role(self, user_id: int, data: ActivateRoleRequest) -> MeResponse:
        side = data.side
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                raise UnauthorizedError("User not found")
            role = previous_role = user["role"]
            if side == "buyer" and role == "supplier":
                role = "both"
            elif side == "supplier" and role == "buyer":
                role = "both"
            elif side == "buyer" and role not in ("buyer", "both"):
                role = "buyer"
            elif side == "supplier" and role not in ("supplier", "both"):
                role = "supplier"
            row = await conn.fetchrow(
                f"""
                UPDATE users AS u
                SET role = $1::user_role,
                    role_id = CASE
                        WHEN u.role_id IS NULL OR u.role_id IN (
                            SELECT id FROM roles WHERE name IN ('buyer', 'supplier', 'both')
                        )
                        THEN (SELECT id FROM roles WHERE name = $1::text)
                        ELSE u.role_id
                    END,
                    updated_at = NOW()
                WHERE u.id = $2
                RETURNING {USER_COLUMNS}
                """,
                role,
                user_id,
            )
            user = dict(row)
            actor = await ActorService(conn).ensure_individual_actor(user, side)
            if side == "supplier":
                # No company: the person sells as themselves and is listed among
                # suppliers. A company employee switches that on in the profile.
                await conn.execute(
                    """
                    INSERT INTO individual_profiles (user_id, accepts_orders)
                    VALUES ($1, NOT EXISTS (SELECT 1 FROM company_users WHERE user_id = $1))
                    ON CONFLICT (user_id) DO UPDATE
                    SET accepts_orders = individual_profiles.accepts_orders
                        OR NOT EXISTS (SELECT 1 FROM company_users WHERE user_id = $1),
                        updated_at = NOW()
                    """,
                    user_id,
                )
            await log_event(
                conn,
                "auth.activate_role",
                "user",
                user_id,
                user_id=user_id,
                actor_id=actor["id"],
                details={"side": side, "before": {"role": previous_role}, "after": {"role": role}},
            )
        return await self.get_me(user_id, active_actor_id=actor["id"])
