from fastapi import FastAPI

from app.modules.auth.api import router
from app.modules.auth.api import create as _create  # noqa: F401
from app.modules.auth.api import read as _read  # noqa: F401
from app.modules.auth.api import update as _update  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
