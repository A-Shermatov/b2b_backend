from app.modules.admin.services.base import AdminServiceBase
from app.modules.admin.services.catalog import AdminCatalogMixin
from app.modules.admin.services.companies import AdminCompaniesMixin
from app.modules.admin.services.contracts import AdminContractsMixin
from app.modules.admin.services.dashboard import AdminDashboardMixin
from app.modules.admin.services.finance import AdminFinanceMixin
from app.modules.admin.services.marketplace import AdminMarketplaceMixin
from app.modules.admin.services.reports import AdminReportsMixin
from app.modules.admin.services.users import AdminUsersMixin


class AdminService(
    AdminUsersMixin,
    AdminDashboardMixin,
    AdminCompaniesMixin,
    AdminCatalogMixin,
    AdminMarketplaceMixin,
    AdminContractsMixin,
    AdminFinanceMixin,
    AdminReportsMixin,
    AdminServiceBase,
):
    pass


__all__ = ["AdminService"]
