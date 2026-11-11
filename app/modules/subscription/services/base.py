import asyncpg

from app.database import pool
from app.modules.subscription.services.common import DEFAULT_SIDE, clean_side


class SubscriptionServiceBase:
    """Works with the plan of one cabinet: the selling one unless told otherwise."""

    def __init__(self, conn: asyncpg.Connection | None = None, side: str = DEFAULT_SIDE) -> None:
        self.conn = conn
        self.side = clean_side(side)

    def connection(self):
        return pool.connection(self.conn)
