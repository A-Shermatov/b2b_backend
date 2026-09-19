from contextlib import asynccontextmanager

import asyncpg

from app.config import get_settings


class DatabasePool:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    @staticmethod
    def dsn(database_url: str) -> str:
        return (
            database_url.replace("postgresql+asyncpg://", "postgresql://")
            .replace("postgres+asyncpg://", "postgresql://")
        )

    async def init(self) -> None:
        if self._pool is not None:
            return
        settings = get_settings()
        self._pool = await asyncpg.create_pool(
            dsn=self.dsn(settings.database_url),
            min_size=1,
            max_size=10,
        )

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    @property
    def ready(self) -> bool:
        return self._pool is not None

    @asynccontextmanager
    async def acquire(self):
        if self._pool is None:
            raise RuntimeError("Database pool is not initialized")
        conn = await self._pool.acquire()
        try:
            yield conn
        finally:
            await self._pool.release(conn)

    @asynccontextmanager
    async def connection(self, conn: asyncpg.Connection | None = None):
        if conn is not None:
            yield conn
            return
        if not self.ready:
            await self.init()
        async with self.acquire() as acquired:
            yield acquired


pool = DatabasePool()
