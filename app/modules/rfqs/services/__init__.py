from app.modules.rfqs.services.base import RfqServiceBase
from app.modules.rfqs.services.create import RfqCreateMixin
from app.modules.rfqs.services.read import RfqReadMixin
from app.modules.rfqs.services.update import RfqUpdateMixin


class RfqService(RfqReadMixin, RfqCreateMixin, RfqUpdateMixin, RfqServiceBase):
    pass


__all__ = ["RfqService"]
