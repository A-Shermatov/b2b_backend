from app.core.pagination import build_page, fetch_page
from app.modules.notifications.services.common import NOTIFICATION_COLUMNS, shape_notification_row


class NotificationReadMixin:
    async def list_for_user(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        *,
        unread: bool | None = None,
    ) -> dict:
        clauses = ["user_id = $1"]
        params: list = [user_id]
        if unread is True:
            clauses.append("read = FALSE")
        elif unread is False:
            clauses.append("read = TRUE")
        where = " AND ".join(clauses)
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                f"SELECT COUNT(*) FROM notifications WHERE {where}",
                f"""
                SELECT {NOTIFICATION_COLUMNS}
                FROM notifications
                WHERE {where}
                ORDER BY created_at DESC
                """,
                params,
                page,
                page_size,
            )
        return build_page([shape_notification_row(row) for row in rows], total, page, page_size)
