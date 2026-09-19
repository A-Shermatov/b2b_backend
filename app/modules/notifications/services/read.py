from app.modules.notifications.schemas import NotificationSchema
from app.modules.notifications.services.common import NOTIFICATION_COLUMNS, shape_notification_row


class NotificationReadMixin:
    async def list_for_user(self, user_id: int) -> list[NotificationSchema]:
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {NOTIFICATION_COLUMNS}
                FROM notifications
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                user_id,
            )
        return [shape_notification_row(row) for row in rows]
