from app.modules.payments.services.base import PaymentServiceBase
from app.modules.payments.services.read import PaymentReadMixin
from app.modules.payments.services.update import PaymentUpdateMixin


class PaymentService(PaymentReadMixin, PaymentUpdateMixin, PaymentServiceBase):
    pass


__all__ = ["PaymentService"]
