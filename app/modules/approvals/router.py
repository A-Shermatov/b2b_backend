from fastapi import FastAPI

from app.modules.approvals.api import buyer_router


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(buyer_router, prefix=f"{prefix}/buyer")
