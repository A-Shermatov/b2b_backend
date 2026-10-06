from fastapi import FastAPI

from app.modules.payments.api import buyer_router, public_router, supplier_router
from app.modules.payments.api import callbacks as _callbacks  # noqa: F401
from app.modules.payments.api import read as _read  # noqa: F401
from app.modules.payments.api import update as _update  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(buyer_router, prefix=f"{prefix}/buyer")
    app.include_router(supplier_router, prefix=f"{prefix}/supplier")
    app.include_router(public_router, prefix=prefix)
