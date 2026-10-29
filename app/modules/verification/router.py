from fastapi import FastAPI

from app.modules.verification.api import router


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
