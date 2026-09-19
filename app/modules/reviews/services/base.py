import asyncpg


class ReviewServiceBase:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
