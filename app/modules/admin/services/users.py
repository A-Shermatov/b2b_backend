from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.auth.schemas import UserPublic
from app.modules.admin.services.common import (
    STAFF_RANK,
    USER_COLUMNS,
    actor_id_of,
    actor_role_of,
    normalize_query,
    pages_for,
)


class AdminUsersMixin:
    async def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        params: list = []
        search_sql = ""
        if normalized:
            params.append(f"%{normalized}%")
            search_idx = len(params)
            search_sql = f"""
                (
                    email ILIKE ${search_idx}
                    OR first_name ILIKE ${search_idx}
                    OR last_name ILIKE ${search_idx}
                    OR (first_name || ' ' || last_name) ILIKE ${search_idx}
                    OR phone ILIKE ${search_idx}
                    OR id::text ILIKE ${search_idx}
            """
            if normalized.isdigit():
                params.append(int(normalized))
                search_sql += f" OR id = ${len(params)}"
            search_sql += ")"

        status_value = status.value if hasattr(status, "value") else status
        filter_sql = search_sql
        if status_value:
            params.append(status_value)
            clause = f"status::text = ${len(params)}"
            filter_sql = f"{filter_sql} AND {clause}" if filter_sql else clause

        where_filter = f"WHERE {filter_sql}" if filter_sql else ""
        where_search = f"WHERE {search_sql}" if search_sql else ""

        async with self.connection() as conn:
            total = int(await conn.fetchval(f"SELECT COUNT(*) FROM users {where_filter}", *params))
            count_rows = await conn.fetch(
                f"SELECT status::text AS status, COUNT(*) AS cnt FROM users {where_search} GROUP BY status",
                *params[: (1 if normalized else 0) + (1 if normalized and normalized.isdigit() else 0)],
            )
            offset_params = list(params)
            offset_params.extend([page_size, (page - 1) * page_size])
            rows = await conn.fetch(
                f"""
                SELECT {USER_COLUMNS}
                FROM users
                {where_filter}
                ORDER BY created_at DESC, id DESC
                LIMIT ${len(offset_params) - 1} OFFSET ${len(offset_params)}
                """,
                *offset_params,
            )

        counts = {row["status"]: int(row["cnt"]) for row in count_rows}
        return {
            "items": [UserPublic.model_validate(dict(row)) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size) if total else max(1, (0 + page_size - 1) // page_size),
            "status_counts": {
                "all": sum(counts.values()),
                "active": counts.get("active", 0),
                "blocked": counts.get("blocked", 0),
                "pending": counts.get("pending", 0),
            },
        }

    async def update_user_status(self, user_id: int, status: str, current_user) -> UserPublic:
        status_value = status.value if hasattr(status, "value") else status
        async with self.connection() as conn:
            user = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
                user_id,
            )
            if not user:
                raise NotFoundError("User not found")
            if user["id"] == actor_id_of(current_user):
                raise ForbiddenError("You cannot change your own account status")
            current_rank = STAFF_RANK.get(actor_role_of(current_user), 0)
            target_rank = STAFF_RANK.get(user["role"], 0)
            if target_rank and target_rank >= current_rank:
                raise ForbiddenError("You cannot change this staff account status")
            updated = await conn.fetchrow(
                f"""
                UPDATE users
                SET status = $1::user_status, updated_at = NOW()
                WHERE id = $2
                RETURNING {USER_COLUMNS}
                """,
                status_value,
                user_id,
            )
        return UserPublic.model_validate(dict(updated))
