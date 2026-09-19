from fastapi import FastAPI

from app.modules.reviews.api import buyer_router, public_router
from app.modules.reviews.api import create as _create  # noqa: F401
from app.modules.reviews.api import read as _read  # noqa: F401


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(buyer_router, prefix=f"{prefix}/buyer")
    app.include_router(public_router, prefix=f"{prefix}/public")
