from fastapi import FastAPI

from app.modules.admin.api import router
from app.modules.admin.api import platform as _platform  # noqa: F401
from app.modules.admin.api import read as _read  # noqa: F401
from app.modules.admin.api import update as _update  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
