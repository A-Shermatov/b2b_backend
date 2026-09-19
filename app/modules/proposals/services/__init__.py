from app.modules.proposals.services.base import ProposalServiceBase
from app.modules.proposals.services.create import ProposalCreateMixin
from app.modules.proposals.services.read import ProposalReadMixin
from app.modules.proposals.services.update import ProposalUpdateMixin


class ProposalService(
    ProposalReadMixin,
    ProposalCreateMixin,
    ProposalUpdateMixin,
    ProposalServiceBase,
):
    pass


__all__ = ["ProposalService"]
