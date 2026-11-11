"""Roles per side of a company: purchases (buyer) and sales (supplier).

A company that both buys and sells has one team. Each member has a company
role, and a director or admin may give them a different role on one side, or
close that side for them ("none"): a purchasing manager then never sees the
company's sales. The director always has full access to both sides.
"""

SIDE_ROLES = ("admin", "moderator", "accountant", "viewer", "none")
SIDE_LABELS_RU = {"buyer": "закупкам", "supplier": "продажам"}


def effective_role_sql(member: str = "cu", side_expr: str = "$3") -> str:
    """SQL expression: the member's role on the given side (may be 'none')."""
    return (
        f"CASE WHEN {member}.role::text = 'director' THEN 'director' "
        f"ELSE COALESCE((SELECT sr.role FROM company_user_side_roles sr "
        f"WHERE sr.company_user_id = {member}.id AND sr.side = {side_expr}), {member}.role::text) END"
    )


def side_access_sql(member: str = "cu", side_expr: str = "$3") -> str:
    """SQL condition: the member may act for the company on this side."""
    return f"({effective_role_sql(member, side_expr)}) <> 'none'"
