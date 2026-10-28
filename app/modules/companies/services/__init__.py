from app.modules.companies.services.base import CompanyServiceBase
from app.modules.companies.services.changes import CompanyChangesMixin
from app.modules.companies.services.create import CompanyCreateMixin
from app.modules.companies.services.read import CompanyReadMixin
from app.modules.companies.services.update import CompanyUpdateMixin


class CompanyService(
    CompanyChangesMixin,
    CompanyReadMixin,
    CompanyCreateMixin,
    CompanyUpdateMixin,
    CompanyServiceBase,
):
    pass


__all__ = ["CompanyService"]
