from app.modules.support.services.base import SupportServiceBase
from app.modules.support.services.create import SupportCreateMixin
from app.modules.support.services.read import SupportReadMixin
from app.modules.support.services.update import SupportUpdateMixin


class SupportService(
    SupportReadMixin, SupportCreateMixin, SupportUpdateMixin, SupportServiceBase
):
    pass


__all__ = ["SupportService"]
