from app.modules.roles.api.common import router
from app.modules.roles.api import create as _create  # noqa: F401
from app.modules.roles.api import read as _read  # noqa: F401
from app.modules.roles.api import update as _update  # noqa: F401

__all__ = ["router"]
