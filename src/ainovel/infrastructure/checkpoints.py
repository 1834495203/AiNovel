"""Official durable LangGraph checkpoints; no graph state is kept only in worker memory."""

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from ainovel.core.config import Settings


class GraphCheckpoints:
    def __init__(self, settings: Settings) -> None:
        url = make_url(settings.database_url).set(drivername="postgresql")
        self.pool = AsyncConnectionPool(
            url.render_as_string(hide_password=False),
            open=False,
            min_size=1,
            max_size=2,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        self._saver: AsyncPostgresSaver | None = None
        self.ready = False
        self.lock = asyncio.Lock()

    @property
    def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise RuntimeError("检查点存储尚未初始化")
        return self._saver

    async def initialize(self) -> None:
        async with self.lock:
            if self.ready:
                return
            await self.pool.open(wait=True)
            self._saver = AsyncPostgresSaver(self.pool)
            async with self.pool.connection() as connection:
                await connection.execute("SELECT pg_advisory_lock(491735102)")
                try:
                    # Serializing migration setup avoids races between independently started workers.
                    await AsyncPostgresSaver(connection).setup()
                finally:
                    await connection.execute("SELECT pg_advisory_unlock(491735102)")
            self.ready = True

    async def close(self) -> None:
        await self.pool.close()
