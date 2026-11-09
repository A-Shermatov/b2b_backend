from fastapi import FastAPI

from app.modules.telegram.api import admin_router


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(admin_router, prefix=prefix)
