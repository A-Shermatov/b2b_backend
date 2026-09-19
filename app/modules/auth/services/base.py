import asyncpg

from app.database import pool


class AuthServiceBase:
    def __init__(self, conn: asyncpg.Connection | None = None) -> None:
        self.conn = conn

    def connection(self):
        return pool.connection(self.conn)
