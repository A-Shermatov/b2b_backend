from app.modules.roles.services.base import RoleServiceBase
from app.modules.roles.services.create import RoleCreateMixin
from app.modules.roles.services.read import RoleReadMixin
from app.modules.roles.services.update import RoleUpdateMixin


class RoleService(RoleUpdateMixin, RoleCreateMixin, RoleReadMixin, RoleServiceBase):
    pass


__all__ = ["RoleService"]
