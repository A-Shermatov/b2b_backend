"""Plain-language classification of HTTP audit rows (``audit_logs.action LIKE 'http.%'``).

Python helpers label single rows; the ``*_sql`` builders express the same rules in SQL
so filters work on rows written before this module existed.
"""

import re

API_SEGMENTS_BEFORE_SIDE = 3

STATUS_GROUPS = (
    "success",
    "invalid",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "rejected",
    "server_error",
)
_EXACT_STATUS = {
    400: "invalid",
    422: "invalid",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
}

ACTION_KINDS = ("login", "register", "create", "update", "delete", "view")
AUTH_KIND_RESOURCES = {"login": "login", "register": "register"}
UPDATE_VERBS = frozenset(
    {
        "accept",
        "action",
        "activate",
        "activate-role",
        "approve",
        "archive",
        "assign",
        "block",
        "cancel",
        "close",
        "complete",
        "delivered",
        "dispute",
        "fund",
        "invite",
        "publish",
        "read",
        "read-all",
        "reject",
        "release",
        "resolve",
        "restore",
        "shortlist",
        "status",
        "switch-actor",
        "switch-company",
        "unarchive",
        "unblock",
        "verify",
        "withdraw",
    }
)

SIDE_SECTIONS: dict[str, str] = {"auth": "auth", "catalog": "catalog"}
SECTION_RESOURCES: dict[str, tuple[str, ...]] = {
    "rfqs": ("rfqs",),
    "proposals": ("proposals",),
    "contracts": ("contracts",),
    "finance": ("payments", "finance", "escrow", "withdrawals"),
    "catalog": ("catalog", "categories"),
    "companies": ("companies", "verification"),
    "staff": ("staff",),
    "roles": ("roles", "permissions"),
    "disputes": ("disputes",),
    "reports": ("reports", "moderation"),
    "reviews": ("reviews",),
    "notifications": ("notifications",),
    "subscription": ("subscription",),
    "users": ("users",),
    "settings": ("settings",),
}
SECTIONS = ("auth", *SECTION_RESOURCES.keys(), "other")
_RESOURCE_TO_SECTION = {
    resource: section for section, resources in SECTION_RESOURCES.items() for resource in resources
}
_ALL_RESOURCES = sorted(_RESOURCE_TO_SECTION)
_SIDE_KEYS = sorted(SIDE_SECTIONS)

_OBJECT_ID = re.compile(r"^(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F-]{4,})$")

PATH_SQL = "rtrim(COALESCE(al.details->>'path', ''), '/')"
SIDE_SQL = f"split_part({PATH_SQL}, '/', {API_SEGMENTS_BEFORE_SIDE + 1})"
RESOURCE_SQL = f"split_part({PATH_SQL}, '/', {API_SEGMENTS_BEFORE_SIDE + 2})"
LAST_SEGMENT_SQL = f"regexp_replace({PATH_SQL}, '^.*/', '')"
STATUS_CODE_SQL = "COALESCE(NULLIF(al.details->>'status_code', '')::int, 200)"


def _segments(path: str | None) -> list[str]:
    return (path or "").rstrip("/").split("/")


def _segment(path: str | None, position: int) -> str:
    parts = _segments(path)
    return parts[position - 1] if len(parts) >= position else ""


def path_side(path: str | None) -> str:
    return _segment(path, API_SEGMENTS_BEFORE_SIDE + 1)


def path_resource(path: str | None) -> str:
    return _segment(path, API_SEGMENTS_BEFORE_SIDE + 2)


def status_group(status_code: int | None) -> str:
    code = int(status_code or 200)
    if code < 400:
        return "success"
    if code >= 500:
        return "server_error"
    return _EXACT_STATUS.get(code, "rejected")


def action_kind(method: str, path: str | None) -> str:
    verb = (method or "").upper()
    if verb == "DELETE":
        return "delete"
    if verb in {"PUT", "PATCH"}:
        return "update"
    if verb != "POST":
        return "view"
    if path_side(path) == "auth" and path_resource(path) in AUTH_KIND_RESOURCES:
        return AUTH_KIND_RESOURCES[path_resource(path)]
    return "update" if _segments(path)[-1] in UPDATE_VERBS else "create"


def section_for(path: str | None) -> str:
    side = path_side(path)
    if side in SIDE_SECTIONS:
        return SIDE_SECTIONS[side]
    return _RESOURCE_TO_SECTION.get(path_resource(path), "other")


def object_id_for(path: str | None) -> str | None:
    for part in reversed(_segments(path)):
        if _OBJECT_ID.match(part):
            return part
    return None


def method_from_action(action: str) -> str:
    return action.split(".", 1)[1].upper() if "." in action else ""


def _param(params: list, value) -> str:
    params.append(value)
    return f"${len(params)}"


def status_sql(group: str, params: list) -> str:
    if group == "success":
        return f"{STATUS_CODE_SQL} < 400"
    if group == "server_error":
        return f"{STATUS_CODE_SQL} >= 500"
    if group == "rejected":
        known = _param(params, sorted(_EXACT_STATUS))
        return (
            f"({STATUS_CODE_SQL} BETWEEN 400 AND 499 "
            f"AND {STATUS_CODE_SQL} <> ALL({known}::int[]))"
        )
    codes = [code for code, name in _EXACT_STATUS.items() if name == group]
    return f"{STATUS_CODE_SQL} = ANY({_param(params, codes)}::int[])"


def _auth_kind_sql(params: list) -> str:
    resources = _param(params, sorted(AUTH_KIND_RESOURCES))
    return f"({SIDE_SQL} = 'auth' AND {RESOURCE_SQL} = ANY({resources}::text[]))"


def kind_sql(kind: str, params: list) -> str:
    if kind == "delete":
        return "al.action = 'http.delete'"
    if kind == "view":
        return "al.action NOT IN ('http.post', 'http.put', 'http.patch', 'http.delete')"
    if kind in AUTH_KIND_RESOURCES:
        return (
            f"(al.action = 'http.post' AND {SIDE_SQL} = 'auth' "
            f"AND {RESOURCE_SQL} = {_param(params, AUTH_KIND_RESOURCES[kind])})"
        )
    verbs = _param(params, sorted(UPDATE_VERBS))
    auth_kind = _auth_kind_sql(params)
    if kind == "update":
        return (
            "(al.action IN ('http.put', 'http.patch') OR "
            f"(al.action = 'http.post' AND {LAST_SEGMENT_SQL} = ANY({verbs}::text[]) "
            f"AND NOT {auth_kind}))"
        )
    return (
        f"(al.action = 'http.post' AND {LAST_SEGMENT_SQL} <> ALL({verbs}::text[]) "
        f"AND NOT {auth_kind})"
    )


def section_sql(section: str, params: list) -> str:
    if section == "other":
        side_keys = _param(params, _SIDE_KEYS)
        resources = _param(params, _ALL_RESOURCES)
        return (
            f"({SIDE_SQL} <> ALL({side_keys}::text[]) "
            f"AND {RESOURCE_SQL} <> ALL({resources}::text[]))"
        )
    parts = []
    sides = [side for side, name in SIDE_SECTIONS.items() if name == section]
    if sides:
        parts.append(f"{SIDE_SQL} = ANY({_param(params, sides)}::text[])")
    resources = SECTION_RESOURCES.get(section)
    if resources:
        side_keys = _param(params, _SIDE_KEYS)
        parts.append(
            f"({SIDE_SQL} <> ALL({side_keys}::text[]) "
            f"AND {RESOURCE_SQL} = ANY({_param(params, list(resources))}::text[]))"
        )
    return f"({' OR '.join(parts)})" if parts else "FALSE"
