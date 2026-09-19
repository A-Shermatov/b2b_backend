ACTOR_COLUMNS = """
    a.id,
    a.kind::text AS kind,
    a.side::text AS side,
    a.user_id,
    a.company_id,
    a.display_name,
    a.trust_level::text AS trust_level,
    a.is_active,
    c.verification_status::text AS verification_status,
    c.title AS company_title
"""


def individual_display_name(user: dict) -> str:
    name = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
    return name or user.get("email") or ""


def trust_level_for_company(verification_status: str | None) -> str:
    if verification_status == "verified":
        return "verified"
    return "standard"


def user_capabilities(role: str) -> dict[str, bool]:
    return {
        "buyer": role in ("buyer", "both"),
        "supplier": role in ("supplier", "both"),
    }


def sides_for_role(role: str) -> list[str]:
    if role == "both":
        return ["buyer", "supplier"]
    if role == "supplier":
        return ["supplier"]
    return ["buyer"]


def shape_actor_row(row) -> dict:
    return dict(row)
