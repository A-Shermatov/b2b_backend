from app.modules.notifications.services.base import NotificationServiceBase
from app.modules.notifications.services.create import NotificationCreateMixin
from app.modules.notifications.services.read import NotificationReadMixin
from app.modules.notifications.services.update import NotificationUpdateMixin


class NotificationService(
    NotificationReadMixin,
    NotificationCreateMixin,
    NotificationUpdateMixin,
    NotificationServiceBase,
):
    pass


__all__ = ["NotificationService"]
