"""Permanent erasure is synchronous and deliberately leaves no operation history."""

from uuid import UUID

from redis.exceptions import RedisError

from ainovel.core.domain import PurgeResult
from ainovel.infrastructure.job_queue import JobQueue
from ainovel.mapper.lifecycle import purge
from ainovel.mapper.store import Store


async def purge_resource(store: Store, queue: JobQueue, resource_id: UUID, *, draft: bool) -> PurgeResult:
    result = await purge(store, resource_id, draft=draft)
    try:
        await queue.remove(result.command_ids)
    except RedisError:
        # Redis contains UUIDs only. Missing commands are safely ignored by workers;
        # a Redis outage cannot undo the committed erasure of all story content.
        pass
    return result
