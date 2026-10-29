from fastapi import FastAPI

# Must be registered before `read`, otherwise /catalog/{item_id} shadows /catalog/items.
from app.modules.admin.api import catalog as _catalog  # noqa: F401
from app.modules.admin.api import clients as _clients  # noqa: F401
from app.modules.admin.api import companies as _companies  # noqa: F401
from app.modules.admin.api import gateway_logs as _gateway_logs  # noqa: F401
from app.modules.admin.api import logs as _logs  # noqa: F401
from app.modules.admin.api import platform as _platform  # noqa: F401
from app.modules.admin.api import read as _read  # noqa: F401
from app.modules.admin.api import router
from app.modules.admin.api import staff as _staff  # noqa: F401
from app.modules.admin.api import update as _update  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
