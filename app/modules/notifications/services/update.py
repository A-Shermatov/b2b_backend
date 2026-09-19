from app.modules.notifications.schemas import NotificationSchema
from app.modules.notifications.services.common import NOTIFICATION_COLUMNS, shape_notification_row


class NotificationUpdateMixin:
    async def mark_read(self, notification_id: int, user_id: int) -> NotificationSchema | None:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE notifications
                SET read = TRUE
                WHERE id = $1 AND user_id = $2
                RETURNING {NOTIFICATION_COLUMNS}
                """,
                notification_id,
                user_id,
            )
        return shape_notification_row(row) if row else None

    async def mark_all_read(self, user_id: int) -> None:
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE notifications
                SET read = TRUE
                WHERE user_id = $1 AND read = FALSE
                """,
                user_id,
            )
