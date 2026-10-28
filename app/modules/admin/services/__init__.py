from app.modules.admin.services.analytics import AdminAnalyticsMixin
from app.modules.admin.services.base import AdminServiceBase
from app.modules.admin.services.categories import AdminCategoriesMixin
from app.modules.admin.services.catalog import AdminCatalogMixin
from app.modules.admin.services.chats import AdminChatsMixin
from app.modules.admin.services.companies import AdminCompaniesMixin
from app.modules.admin.services.contracts import AdminContractsMixin
from app.modules.admin.services.dashboard import AdminDashboardMixin
from app.modules.admin.services.escrow import AdminEscrowMixin
from app.modules.admin.services.finance import AdminFinanceMixin
from app.modules.admin.services.gateway_logs import AdminGatewayLogsMixin
from app.modules.admin.services.logs import AdminLogsMixin
from app.modules.admin.services.marketplace import AdminMarketplaceMixin
from app.modules.admin.services.reports import AdminReportsMixin
from app.modules.admin.services.staff import AdminStaffMixin
from app.modules.admin.services.users import AdminUsersMixin


class AdminService(
    AdminUsersMixin,
    AdminStaffMixin,
    AdminDashboardMixin,
    AdminCompaniesMixin,
    AdminCatalogMixin,
    AdminMarketplaceMixin,
    AdminContractsMixin,
    AdminChatsMixin,
    AdminFinanceMixin,
    AdminReportsMixin,
    AdminCategoriesMixin,
    AdminEscrowMixin,
    AdminAnalyticsMixin,
    AdminLogsMixin,
    AdminGatewayLogsMixin,
    AdminServiceBase,
):
    pass


__all__ = ["AdminService"]
