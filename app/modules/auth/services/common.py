from app.modules.auth.schemas import ActorSummary, UserPublic
from app.modules.actors.services import ActorService, user_capabilities

USER_COLUMNS = """
    id,
    email,
    password_hash,
    first_name,
    last_name,
    phone,
    role::text AS role,
    role_id,
    status::text AS status,
    COALESCE(totp_enabled, false) AS totp_enabled,
    totp_secret,
    COALESCE(totp_backup_codes_hash, '{}'::text[]) AS totp_backup_codes_hash,
    created_at,
    updated_at
"""


def shape_user_public(user: dict) -> UserPublic:
    payload = {
        "id": user["id"],
        "email": user["email"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "phone": user.get("phone"),
        "role": user["role"],
        "status": user["status"],
        "totp_enabled": bool(user.get("totp_enabled")),
        "created_at": user["created_at"],
        "updated_at": user["updated_at"],
    }
    return UserPublic.model_validate(payload)


def actor_to_summary(actor: dict, memberships: dict[int, dict]) -> ActorSummary:
    company_role = None
    verification_status = actor.get("verification_status")
    if actor["kind"] == "company" and actor.get("company_id"):
        membership = memberships.get(actor["company_id"])
        if membership:
            company_role = membership.get("role")
    return ActorSummary(
        id=actor["id"],
        kind=actor["kind"],
        side=actor["side"],
        display_name=actor["display_name"],
        trust_level=actor["trust_level"],
        company_id=actor.get("company_id"),
        verification_status=verification_status,
        company_role=company_role,
    )


async def load_user(conn, user_id: int) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
        user_id,
    )
    return dict(row) if row else None
