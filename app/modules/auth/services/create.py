import hashlib
from datetime import datetime, timezone

import asyncpg

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security_core import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.actors.services import ActorService
from app.modules.auth.schemas import LoginRequest, RegisterRequest, TokenResponse
from app.modules.auth.services.common import USER_COLUMNS, load_user
from app.utils.audit import AuditLogger
from app.utils.redis_client import blacklist_refresh_token, is_refresh_token_blacklisted


class AuthCreateMixin:
    async def register(self, data: RegisterRequest, ip: str | None = None) -> TokenResponse:
        async with self.connection() as conn:
            async with conn.transaction():
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
                        role, status, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6::user_role, 'active', NOW(), NOW()
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
                await AuditLogger(conn).log_action(
                    user_id=user_dict["id"],
                    actor_id=None,
                    company_id=None,
                    action="register",
                    resource_type="user",
                    resource_id=str(user_dict["id"]),
                    ip_address=ip,
                )
        return self._issue_tokens(user_dict)

    async def login(self, data: LoginRequest, ip: str | None = None) -> TokenResponse:
        async with self.connection() as conn:
            user = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM users WHERE email = $1",
                data.email.lower(),
            )
            if not user or not verify_password(data.password, user["password_hash"]):
                raise UnauthorizedError("Invalid email or password")
            if user["status"] == "blocked":
                raise UnauthorizedError("Account is blocked")
            await AuditLogger(conn).log_action(
                user_id=user["id"],
                actor_id=None,
                company_id=None,
                action="login",
                resource_type="user",
                resource_id=str(user["id"]),
                ip_address=ip,
            )
        return self._issue_tokens(dict(user))

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_token(refresh_token)
        except ValueError as exc:
            raise UnauthorizedError("Invalid refresh token") from exc
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Invalid token type")
        jti_hash = hashlib.sha256(payload["jti"].encode()).hexdigest()
        if await is_refresh_token_blacklisted(jti_hash):
            raise UnauthorizedError("Token revoked")
        user_id = int(payload["sub"])
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
        if not user:
            raise UnauthorizedError("User not found")
        return self._issue_tokens(user)

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
        await blacklist_refresh_token(jti_hash, expires_at)

    def _issue_tokens(self, user: dict) -> TokenResponse:
        access = create_access_token(str(user["id"]), {"role": user["role"]})
        refresh, _ = create_refresh_token(str(user["id"]))
        return TokenResponse(access_token=access, refresh_token=refresh)
