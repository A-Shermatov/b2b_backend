from app.modules.auth.services.base import AuthServiceBase
from app.modules.auth.services.create import AuthCreateMixin
from app.modules.auth.services.read import AuthReadMixin
from app.modules.auth.services.update import AuthUpdateMixin


class AuthService(AuthReadMixin, AuthCreateMixin, AuthUpdateMixin, AuthServiceBase):
    pass


__all__ = ["AuthService"]
