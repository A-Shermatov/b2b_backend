from fastapi import FastAPI

from app.modules.settings.api import admin_router, public_router


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(admin_router, prefix=prefix)
    app.include_router(public_router, prefix=f"{prefix}/public")
