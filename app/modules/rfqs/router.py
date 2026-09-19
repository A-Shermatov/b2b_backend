from fastapi import FastAPI

from app.modules.rfqs.api import buyer_router, public_router, supplier_router
from app.modules.rfqs.api import create as _create  # noqa: F401
from app.modules.rfqs.api import read as _read  # noqa: F401
from app.modules.rfqs.api import update as _update  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(buyer_router, prefix=f"{prefix}/buyer")
    app.include_router(supplier_router, prefix=f"{prefix}/supplier")
    app.include_router(public_router, prefix=f"{prefix}/public")
