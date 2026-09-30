import re

import asyncpg

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import actor_id_of, log_admin

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

CATEGORY_COLUMNS = """
    c.id, c.parent_id, c.name, c.slug,
    (SELECT COUNT(*) FROM catalog_items ci
     WHERE ci.category_id = c.id AND ci.status::text <> 'deleted') AS catalog_items_count,
    (SELECT COUNT(*) FROM company_categories cc WHERE cc.category_id = c.id) AS companies_count,
    (SELECT COUNT(*) FROM categories ch WHERE ch.parent_id = c.id) AS children_count
"""


def _serialize_category(row) -> dict:
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "name": row["name"],
        "slug": row["slug"],
        "catalog_items_count": int(row["catalog_items_count"]),
        "companies_count": int(row["companies_count"]),
        "children_count": int(row["children_count"]),
    }


def _clean_name(name: str) -> str:
    value = (name or "").strip()
    if not value:
        raise ValidationError("Название категории обязательно")
    return value


def _clean_slug(slug: str) -> str:
    value = (slug or "").strip().lower()
    if not SLUG_RE.match(value):
        raise ValidationError("Slug может содержать только латиницу, цифры и дефисы")
    return value


async def _load_category(conn, category_id: int) -> dict:
    row = await conn.fetchrow(
        f"SELECT {CATEGORY_COLUMNS} FROM categories c WHERE c.id = $1",
        category_id,
    )
    if not row:
        raise NotFoundError("Category not found")
    return _serialize_category(row)


async def _ensure_parent(conn, parent_id: int | None, category_id: int | None = None) -> None:
    if parent_id is None:
        return
    if parent_id == category_id:
        raise ValidationError("Категория не может быть родителем самой себя")
    exists = await conn.fetchval("SELECT 1 FROM categories WHERE id = $1", parent_id)
    if not exists:
        raise ValidationError("Родительская категория не найдена")
    if category_id is None:
        return
    creates_cycle = await conn.fetchval(
        """
        WITH RECURSIVE ancestors AS (
            SELECT id, parent_id FROM categories WHERE id = $1
            UNION
            SELECT c.id, c.parent_id
            FROM categories c
            JOIN ancestors a ON c.id = a.parent_id
        )
        SELECT 1 FROM ancestors WHERE id = $2 LIMIT 1
        """,
        parent_id,
        category_id,
    )
    if creates_cycle:
        raise ValidationError("Нельзя переместить категорию внутрь её дочерней категории")


class AdminCategoriesMixin:
    async def list_categories(self) -> dict:
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {CATEGORY_COLUMNS}
                FROM categories c
                ORDER BY c.parent_id NULLS FIRST, c.name
                """
            )
        return {"items": [_serialize_category(row) for row in rows]}

    async def create_category(self, data, current_user) -> dict:
        ensure_permission(current_user, "admin_catalog.update")
        name = _clean_name(data.name)
        slug = _clean_slug(data.slug)
        async with self.connection() as conn:
            async with conn.transaction():
                await _ensure_parent(conn, data.parent_id)
                try:
                    category_id = await conn.fetchval(
                        """
                        INSERT INTO categories (parent_id, name, slug)
                        VALUES ($1, $2, $3)
                        RETURNING id
                        """,
                        data.parent_id,
                        name,
                        slug,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise ConflictError("Категория с таким slug уже существует") from exc
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.category.create",
                    "category",
                    category_id,
                    {"name": name, "slug": slug, "parent_id": data.parent_id},
                )
                return await _load_category(conn, category_id)

    async def update_category(self, category_id: int, data, current_user) -> dict:
        ensure_permission(current_user, "admin_catalog.update")
        fields = data.model_fields_set
        async with self.connection() as conn:
            async with conn.transaction():
                current = await _load_category(conn, category_id)
                name = _clean_name(data.name) if "name" in fields and data.name is not None else current["name"]
                slug = _clean_slug(data.slug) if "slug" in fields and data.slug is not None else current["slug"]
                parent_id = data.parent_id if "parent_id" in fields else current["parent_id"]
                if parent_id != current["parent_id"]:
                    await _ensure_parent(conn, parent_id, category_id)
                try:
                    await conn.execute(
                        "UPDATE categories SET name = $1, slug = $2, parent_id = $3 WHERE id = $4",
                        name,
                        slug,
                        parent_id,
                        category_id,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise ConflictError("Категория с таким slug уже существует") from exc
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.category.update",
                    "category",
                    category_id,
                    {
                        "before": {k: current[k] for k in ("name", "slug", "parent_id")},
                        "after": {"name": name, "slug": slug, "parent_id": parent_id},
                    },
                )
                return await _load_category(conn, category_id)

    async def delete_category(self, category_id: int, current_user) -> dict:
        ensure_permission(current_user, "admin_catalog.delete")
        async with self.connection() as conn:
            async with conn.transaction():
                current = await _load_category(conn, category_id)
                all_items = await conn.fetchval(
                    "SELECT COUNT(*) FROM catalog_items WHERE category_id = $1",
                    category_id,
                )
                usage = []
                if current["children_count"]:
                    usage.append(f"подкатегорий: {current['children_count']}")
                if all_items:
                    usage.append(f"товаров и услуг: {all_items}")
                if current["companies_count"]:
                    usage.append(f"компаний: {current['companies_count']}")
                if usage:
                    raise ConflictError(f"Категория используется ({', '.join(usage)})")
                await conn.execute("DELETE FROM categories WHERE id = $1", category_id)
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.category.delete",
                    "category",
                    category_id,
                    {"name": current["name"], "slug": current["slug"]},
                )
        return {"id": category_id, "deleted": True}
