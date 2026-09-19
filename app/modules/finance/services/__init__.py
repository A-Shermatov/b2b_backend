from app.modules.finance.services.base import FinanceServiceBase
from app.modules.finance.services.common import SUBSCRIPTION_PLAN_AMOUNTS
from app.modules.finance.services.create import FinanceCreateMixin
from app.modules.finance.services.ledger import FinanceLedgerMixin
from app.modules.finance.services.read import FinanceReadMixin


class FinanceLedgerService(FinanceLedgerMixin, FinanceServiceBase):
    pass


class SupplierFinanceService(
    FinanceReadMixin,
    FinanceCreateMixin,
    FinanceServiceBase,
):
    pass


__all__ = [
    "FinanceLedgerService",
    "SupplierFinanceService",
    "SUBSCRIPTION_PLAN_AMOUNTS",
]
