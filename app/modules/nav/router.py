from fastapi import FastAPI

from app.modules.nav.api import admin_router, buyer_router, supplier_router


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(buyer_router, prefix=f"{prefix}/buyer")
    app.include_router(supplier_router, prefix=f"{prefix}/supplier")
    app.include_router(admin_router, prefix=prefix)
