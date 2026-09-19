import asyncpg

from app.database import pool


class CategoryServiceBase:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn


class CatalogItemServiceBase:
    def __init__(self, conn: asyncpg.Connection | None = None) -> None:
        self.conn = conn

    def connection(self):
        return pool.connection(self.conn)
