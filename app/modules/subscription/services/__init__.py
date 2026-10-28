from app.modules.subscription.services.base import SubscriptionServiceBase
from app.modules.subscription.services.payment import SubscriptionPaymentMixin
from app.modules.subscription.services.read import SubscriptionReadMixin
from app.modules.subscription.services.update import SubscriptionUpdateMixin


class SubscriptionService(
    SubscriptionPaymentMixin,
    SubscriptionReadMixin,
    SubscriptionUpdateMixin,
    SubscriptionServiceBase,
):
    pass


__all__ = ["SubscriptionService"]
