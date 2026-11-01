"""Redis transport + PostgreSQL outbox recovery; delivery is at least once, commits are idempotent."""

from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError


class JobQueue:
    def __init__(self, url: str, prefix: str = "ainovel") -> None:
        self.redis = Redis.from_url(url, decode_responses=True)
        self.prefix = prefix
        self.waiting = f"{prefix}:jobs:waiting"
        self.processing = f"{prefix}:jobs:processing"
        self.worker_alive = f"{prefix}:worker:alive"

    def marker(self, command_id: UUID) -> str:
        return f"{self.prefix}:queued:{command_id}"

    async def enqueue(self, command_id: UUID) -> None:
        key = self.marker(command_id)
        if await self.redis.set(key, "1", nx=True, ex=60):
            # If push fails, DB outbox will retry after the marker expires.
            await self.redis.lpush(self.waiting, str(command_id))

    async def receive(self) -> UUID | None:
        value = await self.redis.brpoplpush(self.waiting, self.processing, timeout=2)
        return UUID(value) if value else None

    async def ack(self, command_id: UUID) -> None:
        await self.redis.lrem(self.processing, 1, str(command_id))
        await self.redis.delete(self.marker(command_id))

    async def health(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except RedisError:
            return False

    async def remove(self, command_ids: list[UUID]) -> None:
        if not command_ids:
            return
        async with self.redis.pipeline(transaction=True) as pipeline:
            for command_id in command_ids:
                pipeline.lrem(self.waiting, 0, str(command_id))
                pipeline.lrem(self.processing, 0, str(command_id))
                pipeline.delete(self.marker(command_id))
            await pipeline.execute()

    async def close(self) -> None:
        await self.redis.aclose()
