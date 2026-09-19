from app.modules.contracts.services.base import ContractServiceBase
from app.modules.contracts.services.common import _custom_milestones, _default_milestones
from app.modules.contracts.services.create import ContractCreateMixin
from app.modules.contracts.services.read import ContractReadMixin
from app.modules.contracts.services.update import ContractUpdateMixin


class ContractService(
    ContractReadMixin,
    ContractCreateMixin,
    ContractUpdateMixin,
    ContractServiceBase,
):
    pass


__all__ = ["ContractService", "_custom_milestones", "_default_milestones"]
