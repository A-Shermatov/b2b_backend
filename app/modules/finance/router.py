from fastapi import FastAPI

from app.modules.finance.api import supplier_router
from app.modules.finance.api import create as _create  # noqa: F401
from app.modules.finance.api import read as _read  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(supplier_router, prefix=f"{prefix}/supplier")
