"""Invitations to a company team.

A director or admin invites a person by email and picks the access level. The
person joins only after accepting: from the link (works for people who are not
registered yet) or from their cabinet. Until then the team list shows the
invitation as waiting, and it can be cancelled or sent again.

Emails are not confirmed on the platform, so an address alone proves nothing.
An invitation can be accepted with its link, or without the link only by an
account that already existed when the invitation was sent — the same trust the
old "add by email" had.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

from app.config import get_settings
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.core.permissions import COMPANY_ROLE_LABELS_RU
from app.modules.companies.schemas import (
    AddTeamMemberRequest,
    CompanyInvitationSchema,
    InvitationPreview,
    MyInvitationSchema,
)
from app.modules.companies.services.common import (
    COMPANY_ROLES,
    ensure_can_change_member,
    ensure_can_manage,
)
from app.modules.notifications.services import NotificationService
from app.utils import redis_client
from app.utils.audit import log_event
from app.utils.storage import public_file_url

INVITATION_TTL_DAYS = 14
TEAM_INVITES_PER_HOUR = 20
INVITABLE_ROLES = COMPANY_ROLES - {"director"}

GONE_MESSAGE = "Приглашение больше не действует. Попросите руководителя компании отправить новое"
OTHER_EMAIL_MESSAGE = (
    "Приглашение выписано на другой адрес. Войдите под тем адресом, на который оно пришло"
)

INVITATION_SELECT = """
    SELECT
        i.id, i.company_id, i.email, i.role, i.status, i.invited_by,
        i.created_at, i.expires_at, i.responded_at,
        c.title AS company_title, c.logo AS company_logo,
        NULLIF(TRIM(CONCAT_WS(' ', iu.first_name, iu.last_name)), '') AS invited_by_name
    FROM company_invitations i
    JOIN companies c ON c.id = i.company_id
    LEFT JOIN users iu ON iu.id = i.invited_by
"""


@dataclass
class InvitationEmail:
    to: str
    subject: str
    text: str


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_link(token: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}/invite/{token}"


def role_label(role: str) -> str:
    return COMPANY_ROLE_LABELS_RU.get(role, role)


def _shown_status(row) -> str:
    """Stored status, with a waiting invitation past its date shown as expired."""
    if row["status"] == "pending" and row["expires_at"] <= datetime.now(timezone.utc):
        return "expired"
    return row["status"]


def _to_schema(row, token: str | None = None) -> CompanyInvitationSchema:
    return CompanyInvitationSchema(
        id=row["id"],
        company_id=row["company_id"],
        email=row["email"],
        role=row["role"],
        status=_shown_status(row),
        invited_by_name=row["invited_by_name"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        responded_at=row["responded_at"],
        invite_url=invite_link(token) if token else None,
    )


def _build_email(row, token: str) -> InvitationEmail:
    company = row["company_title"]
    who = row["invited_by_name"] or "Руководитель компании"
    return InvitationEmail(
        to=row["email"],
        subject=f"Приглашение в компанию «{company}» на Brend Market",
        text=(
            f"{who} приглашает вас в команду компании «{company}» на Brend Market.\n"
            f"Уровень доступа: {role_label(row['role'])}.\n\n"
            f"Чтобы принять приглашение или отказаться, откройте ссылку:\n"
            f"{invite_link(token)}\n\n"
            f"Ссылка действует {INVITATION_TTL_DAYS} дней. "
            "Если вы не ждали этого письма, просто не открывайте ссылку."
        ),
    )


class CompanyInvitationMixin:
    async def _create_invitation(
        self, conn, company_id: int, inviter_id: int, email: str, role: str
    ) -> tuple[dict, str]:
        """Insert (or reopen) the invitation for this address. Returns the row and the token."""
        email = email.strip().lower()
        if role not in INVITABLE_ROLES:
            raise ValidationError("Invalid company role")
        already_member = await conn.fetchval(
            """
            SELECT 1 FROM company_users cu
            JOIN users u ON u.id = cu.user_id
            WHERE cu.company_id = $1 AND lower(u.email) = $2
            """,
            company_id,
            email,
        )
        if already_member:
            raise ConflictError("Этот человек уже в команде")
        waiting = await conn.fetchrow(
            """
            SELECT id, expires_at FROM company_invitations
            WHERE company_id = $1 AND lower(email) = $2 AND status = 'pending'
            FOR UPDATE
            """,
            company_id,
            email,
        )
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=INVITATION_TTL_DAYS)
        if waiting:
            if waiting["expires_at"] > datetime.now(timezone.utc):
                raise ConflictError(
                    "Приглашение на этот адрес уже отправлено. "
                    "Его можно отправить ещё раз из списка приглашений"
                )
            # The old invitation ran out: the same row starts over.
            await conn.execute(
                """
                UPDATE company_invitations
                SET role = $2, token_hash = $3, invited_by = $4,
                    created_at = NOW(), expires_at = $5
                WHERE id = $1
                """,
                waiting["id"],
                role,
                hash_invite_token(token),
                inviter_id,
                expires_at,
            )
            invitation_id = waiting["id"]
        else:
            try:
                invitation_id = await conn.fetchval(
                    """
                    INSERT INTO company_invitations (
                        company_id, email, role, token_hash, invited_by, expires_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    company_id,
                    email,
                    role,
                    hash_invite_token(token),
                    inviter_id,
                    expires_at,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictError("Приглашение на этот адрес уже отправлено") from exc
        await log_event(
            conn,
            "company.member.invite",
            "company",
            company_id,
            user_id=inviter_id,
            company_id=company_id,
            details={"invitation_id": invitation_id, "role": role},
        )
        row = await conn.fetchrow(INVITATION_SELECT + " WHERE i.id = $1", invitation_id)
        return dict(row), token

    async def _notify_invited(self, row: dict) -> None:
        """Tell the invited person in their cabinet, when they already have an account."""
        async with self.connection() as conn:
            user_id = await conn.fetchval(
                "SELECT id FROM users WHERE lower(email) = $1", row["email"].lower()
            )
        if not user_id:
            return
        await NotificationService().notify_user(
            user_id,
            "system",
            f"Вас пригласили в компанию «{row['company_title']}»",
            f"Уровень доступа: {role_label(row['role'])}. "
            "Откройте приглашение, чтобы принять его или отказаться.",
            href="/invitations",
        )

    async def invite_team_member(
        self, company_id: int, user_id: int, data: AddTeamMemberRequest
    ) -> tuple[CompanyInvitationSchema, InvitationEmail]:
        if data.role == "director":
            raise ValidationError("Cannot assign director role")
        if data.role not in INVITABLE_ROLES:
            raise ValidationError("Invalid company role")
        async with self.connection() as conn:
            async with conn.transaction():
                await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
                await ensure_can_change_member(conn, company_id, user_id, new_role=data.role)
                if not await redis_client.check_rate_limit(
                    f"team_add:{user_id}", TEAM_INVITES_PER_HOUR, 3600
                ):
                    raise RateLimitError(
                        "Слишком много приглашений за час. Попробуйте позже"
                    )
                row, token = await self._create_invitation(
                    conn, company_id, user_id, data.email, data.role
                )
        await self._notify_invited(row)
        return _to_schema(row, token), _build_email(row, token)

    async def list_invitations(
        self, company_id: int, user_id: int
    ) -> list[CompanyInvitationSchema]:
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
            rows = await conn.fetch(
                INVITATION_SELECT
                + """
                WHERE i.company_id = $1
                  AND (
                      i.status = 'pending'
                      OR (i.status = 'declined' AND i.responded_at > NOW() - INTERVAL '30 days')
                  )
                ORDER BY i.created_at DESC, i.id DESC
                """,
                company_id,
            )
        return [_to_schema(row) for row in rows]

    async def resend_invitation(
        self, company_id: int, user_id: int, invitation_id: int
    ) -> tuple[CompanyInvitationSchema, InvitationEmail]:
        token = secrets.token_urlsafe(32)
        async with self.connection() as conn:
            async with conn.transaction():
                await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
                current = await conn.fetchrow(
                    """
                    SELECT id, role FROM company_invitations
                    WHERE id = $1 AND company_id = $2 AND status = 'pending'
                    FOR UPDATE
                    """,
                    invitation_id,
                    company_id,
                )
                if not current:
                    raise NotFoundError("Приглашение не найдено или на него уже ответили")
                await ensure_can_change_member(
                    conn, company_id, user_id, new_role=current["role"]
                )
                if not await redis_client.check_rate_limit(
                    f"team_add:{user_id}", TEAM_INVITES_PER_HOUR, 3600
                ):
                    raise RateLimitError("Слишком много приглашений за час. Попробуйте позже")
                # A new link; the old one stops working.
                await conn.execute(
                    """
                    UPDATE company_invitations
                    SET token_hash = $2, invited_by = $3, created_at = NOW(),
                        expires_at = NOW() + make_interval(days => $4)
                    WHERE id = $1
                    """,
                    invitation_id,
                    hash_invite_token(token),
                    user_id,
                    INVITATION_TTL_DAYS,
                )
                row = dict(
                    await conn.fetchrow(INVITATION_SELECT + " WHERE i.id = $1", invitation_id)
                )
        await self._notify_invited(row)
        return _to_schema(row, token), _build_email(row, token)

    async def cancel_invitation(self, company_id: int, user_id: int, invitation_id: int) -> None:
        async with self.connection() as conn:
            async with conn.transaction():
                await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
                cancelled = await conn.fetchval(
                    """
                    UPDATE company_invitations
                    SET status = 'cancelled', responded_at = NOW()
                    WHERE id = $1 AND company_id = $2 AND status = 'pending'
                    RETURNING id
                    """,
                    invitation_id,
                    company_id,
                )
                if not cancelled:
                    raise NotFoundError("Приглашение не найдено или на него уже ответили")
                await log_event(
                    conn,
                    "company.member.invite_cancel",
                    "company",
                    company_id,
                    user_id=user_id,
                    company_id=company_id,
                    details={"invitation_id": invitation_id},
                )

    # --- the invited person -------------------------------------------------

    async def my_invitations(self, user_id: int) -> list[MyInvitationSchema]:
        async with self.connection() as conn:
            rows = await conn.fetch(
                INVITATION_SELECT
                + """
                JOIN users me ON me.id = $1 AND lower(me.email) = lower(i.email)
                WHERE i.status = 'pending' AND i.expires_at > NOW()
                  AND me.created_at <= i.created_at
                ORDER BY i.created_at DESC
                """,
                user_id,
            )
        return [
            MyInvitationSchema(
                id=row["id"],
                company_id=row["company_id"],
                company_title=row["company_title"],
                company_logo=public_file_url(row["company_logo"]) if row["company_logo"] else None,
                role=row["role"],
                invited_by_name=row["invited_by_name"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]

    async def preview_invitation(self, token: str) -> InvitationPreview:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                INVITATION_SELECT + " WHERE i.token_hash = $1", hash_invite_token(token)
            )
        if not row:
            raise NotFoundError(GONE_MESSAGE)
        return InvitationPreview(
            status=_shown_status(row),
            company_title=row["company_title"],
            company_logo=public_file_url(row["company_logo"]) if row["company_logo"] else None,
            role=row["role"],
            email=row["email"],
            invited_by_name=row["invited_by_name"],
            expires_at=row["expires_at"],
        )

    async def _load_for_answer(
        self, conn, user: dict, *, invitation_id: int | None, token: str | None
    ) -> dict:
        """The waiting invitation this user may answer, locked for the answer."""
        if token:
            row = await conn.fetchrow(
                INVITATION_SELECT + " WHERE i.token_hash = $1 FOR UPDATE OF i",
                hash_invite_token(token),
            )
        else:
            row = await conn.fetchrow(
                INVITATION_SELECT + " WHERE i.id = $1 FOR UPDATE OF i", invitation_id
            )
            # Without the link: only an account that existed when the invitation was sent.
            if row and user["created_at"] > row["created_at"]:
                row = None
        if not row or (not token and row["email"].lower() != user["email"].lower()):
            raise NotFoundError(GONE_MESSAGE)
        if row["email"].lower() != user["email"].lower():
            raise ForbiddenError(OTHER_EMAIL_MESSAGE, code="invitation_other_email")
        if _shown_status(row) != "pending":
            raise ConflictError(GONE_MESSAGE)
        return dict(row)

    async def join_by_invitation(self, conn, row: dict, user: dict) -> None:
        """Make the user a member with the invited role. Runs inside the caller's transaction."""
        sides = [
            r["side"]
            for r in await conn.fetch(
                """
                SELECT side::text AS side FROM actors
                WHERE company_id = $1 AND kind::text = 'company' AND is_active = TRUE
                """,
                row["company_id"],
            )
        ]
        # Working for a company that sells needs the supplier side of the account
        # (and the buyer side for one that buys).
        await self._ensure_roles_and_actors(conn, user, sides)
        await conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role)
            SELECT $1, $2, $3::company_role
            WHERE NOT EXISTS (
                SELECT 1 FROM company_users WHERE company_id = $1 AND user_id = $2
            )
            """,
            row["company_id"],
            user["id"],
            row["role"],
        )
        await conn.execute(
            """
            UPDATE company_invitations
            SET status = 'accepted', responded_at = NOW(), accepted_user_id = $2
            WHERE id = $1
            """,
            row["id"],
            user["id"],
        )
        # From now on the cabinet opens in the company, unless the person also
        # takes orders as a private person: then it stays their own.
        await conn.execute(
            """
            INSERT INTO individual_profiles (user_id, prefers_company)
            VALUES ($1, TRUE)
            ON CONFLICT (user_id) DO UPDATE
            SET prefers_company = NOT individual_profiles.accepts_orders
                OR individual_profiles.prefers_company
            """,
            user["id"],
        )
        await log_event(
            conn,
            "company.member.join",
            "company",
            row["company_id"],
            user_id=user["id"],
            company_id=row["company_id"],
            details={"invitation_id": row["id"], "role": row["role"]},
        )

    async def _tell_inviter(self, row: dict, user: dict, accepted: bool) -> None:
        if not row.get("invited_by"):
            return
        name = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
        who = f"{name} ({user['email']})" if name else user["email"]
        if accepted:
            title = "Приглашение принято"
            body = f"{who} теперь в команде компании «{row['company_title']}» — {role_label(row['role']).lower()}."
        else:
            title = "От приглашения отказались"
            body = f"{who} отказался от приглашения в компанию «{row['company_title']}»."
        await NotificationService().notify_user(
            row["invited_by"], "system", title, body, company_id=row["company_id"]
        )

    async def accept_invitation(
        self, user_id: int, *, invitation_id: int | None = None, token: str | None = None
    ) -> int:
        """Join the company. Returns the company id."""
        async with self.connection() as conn:
            async with conn.transaction():
                user = await conn.fetchrow(
                    """
                    SELECT id, email, first_name, last_name, role::text AS role, created_at
                    FROM users WHERE id = $1
                    """,
                    user_id,
                )
                row = await self._load_for_answer(
                    conn, dict(user), invitation_id=invitation_id, token=token
                )
                await self.join_by_invitation(conn, row, dict(user))
        await self._tell_inviter(row, dict(user), accepted=True)
        return row["company_id"]

    async def decline_invitation(
        self, user_id: int, *, invitation_id: int | None = None, token: str | None = None
    ) -> None:
        async with self.connection() as conn:
            async with conn.transaction():
                user = await conn.fetchrow(
                    """
                    SELECT id, email, first_name, last_name, role::text AS role, created_at
                    FROM users WHERE id = $1
                    """,
                    user_id,
                )
                row = await self._load_for_answer(
                    conn, dict(user), invitation_id=invitation_id, token=token
                )
                await conn.execute(
                    """
                    UPDATE company_invitations
                    SET status = 'declined', responded_at = NOW()
                    WHERE id = $1
                    """,
                    row["id"],
                )
                await log_event(
                    conn,
                    "company.member.invite_decline",
                    "company",
                    row["company_id"],
                    user_id=user_id,
                    company_id=row["company_id"],
                    details={"invitation_id": row["id"]},
                )
        await self._tell_inviter(row, dict(user), accepted=False)
