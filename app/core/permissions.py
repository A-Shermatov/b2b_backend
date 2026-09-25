"""Global RBAC: permission names, matrix buckets, runtime checks."""

from app.core.exceptions import ForbiddenError

READ_ACTIONS = frozenset({"view"})
WRITE_ACTIONS = frozenset(
    {
        "create",
        "update",
        "publish",
        "close",
        "invite",
        "accept",
        "fund",
        "approve",
        "manage",
        "block",
        "force_complete",
        "suspend",
        "export",
    }
)
DELETE_ACTIONS = frozenset({"delete"})

RESOURCE_ALIASES_RU: dict[str, str] = {
    "company_profile": "Профиль компании",
    "company_team": "Команда компании",
    "company_certificates": "Сертификаты компании",
    "rfqs": "Запросы",
    "proposals": "Предложения",
    "contracts": "Контракты",
    "payments": "Платежи",
    "catalog": "Каталог",
    "finance": "Финансы",
    "subscription": "Подписка",
    "reviews": "Отзывы",
    "admin_users": "Пользователи",
    "admin_companies": "Компании",
    "admin_catalog": "Модерация каталога",
    "admin_rfqs": "Модерация запросов",
    "admin_proposals": "Модерация предложений",
    "admin_contracts": "Модерация контрактов",
    "admin_disputes": "Споры",
    "admin_finance": "Финансы платформы",
    "admin_reports": "Жалобы",
    "admin_dashboard": "Дашборд",
    "roles": "Роли",
}

PROTECTED_ROLE_NAMES = frozenset({"superadmin"})


def action_bucket(action: str) -> str | None:
    name = (action or "").lower()
    if name in READ_ACTIONS:
        return "read"
    if name in WRITE_ACTIONS:
        return "write"
    if name in DELETE_ACTIONS:
        return "delete"
    return None


def resource_alias_ru(resource: str) -> str:
    return RESOURCE_ALIASES_RU.get(resource, resource.replace("_", " ").title())


def is_superadmin(user) -> bool:
    role = user["role"] if isinstance(user, dict) else getattr(user, "role", None)
    return (role or "").lower() == "superadmin"


def permission_names(user) -> set[str]:
    raw = user["permissions"] if isinstance(user, dict) else getattr(user, "permissions", ())
    return set(raw or ())


def has_permission(user, permission: str) -> bool:
    if is_superadmin(user):
        return True
    return permission in permission_names(user)


def ensure_permission(user, permission: str) -> None:
    if not has_permission(user, permission):
        raise ForbiddenError("Insufficient permissions")


async def fetch_permission_names(conn, *, role_id: int | None, role_name: str) -> set[str]:
    if (role_name or "").lower() == "superadmin":
        rows = await conn.fetch("SELECT name FROM permissions WHERE is_active = TRUE")
        return {row["name"] for row in rows}
    if role_id:
        rows = await conn.fetch(
            """
            SELECT p.name
            FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            WHERE rp.role_id = $1 AND rp.is_active = TRUE AND p.is_active = TRUE
            """,
            role_id,
        )
        return {row["name"] for row in rows}
    rows = await conn.fetch(
        """
        SELECT p.name
        FROM permissions p
        JOIN role_permissions rp ON rp.permission_id = p.id
        JOIN roles r ON r.id = rp.role_id
        WHERE r.name = $1 AND r.is_active = TRUE
          AND rp.is_active = TRUE AND p.is_active = TRUE
        """,
        role_name,
    )
    return {row["name"] for row in rows}
