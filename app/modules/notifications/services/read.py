from app.core.pagination import build_page, fetch_page
from app.modules.notifications.services.common import NOTIFICATION_COLUMNS, shape_notification_row


class NotificationReadMixin:
    async def list_for_user(self, user_id: int, page: int = 1, page_size: int = 20) -> dict:
        async with self.connection() as conn:
            total, rows = await fetch_page(
                conn,
                "SELECT COUNT(*) FROM notifications WHERE user_id = $1",
                f"""
                SELECT {NOTIFICATION_COLUMNS}
                FROM notifications
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                [user_id],
                page,
                page_size,
            )
        return build_page([shape_notification_row(row) for row in rows], total, page, page_size)
