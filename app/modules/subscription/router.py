from fastapi import FastAPI

from app.modules.subscription.api import buyer_router, supplier_router
from app.modules.subscription.api import read as _read  # noqa: F401
from app.modules.subscription.api import update as _update  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(supplier_router, prefix=f"{prefix}/supplier")
    app.include_router(buyer_router, prefix=f"{prefix}/buyer")
