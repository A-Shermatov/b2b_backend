from app.modules.subscription.services.base import SubscriptionServiceBase
from app.modules.subscription.services.read import SubscriptionReadMixin
from app.modules.subscription.services.update import SubscriptionUpdateMixin


class SubscriptionService(SubscriptionReadMixin, SubscriptionUpdateMixin, SubscriptionServiceBase):
    pass


__all__ = ["SubscriptionService"]
