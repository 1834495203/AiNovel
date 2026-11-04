"""Command consumption and PostgreSQL outbox recovery."""

import asyncio
import logging
from contextlib import aclosing, suppress
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import load_only

from ainovel.bootstrap.runtime import Runtime
from ainovel.core.domain import CommandView, Event
from ainovel.core.errors import DraftRevisionError, InvalidCheckpointError
from ainovel.infrastructure.job_queue import JobQueue
from ainovel.mapper.models import CommandRow

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, runtime: Runtime, queue: JobQueue) -> None:
        self.runtime = runtime
        self.queue = queue

    async def recover(self) -> None:
        while True:
            try:
                await self.recover_once()
            except Exception:
                logger.exception("Outbox recovery failed; retrying")
            await asyncio.sleep(10)

    async def recover_once(self) -> None:
        await self.queue.redis.set(self.queue.worker_alive, "1", ex=30)
        for command_id in await self.runtime.store.pending():
            await self.queue.enqueue(command_id)
        # Processing IDs can remain after process death; inspect lifecycle columns
        # only, so one malformed checkpoint cannot block the outbox scan.
        for raw_id in await self.queue.redis.lrange(self.queue.processing, 0, -1):
            lifecycle = await self.runtime.store.command_lifecycle(UUID(raw_id))
            if lifecycle is None or lifecycle.status in ("completed", "failed", "waiting"):
                await self.queue.redis.lrem(self.queue.processing, 0, raw_id)

    async def process(self, command_id: UUID) -> None:
        store = self.runtime.store
        view = await self.load_command(command_id)
        if view is None:
            return
        if view.undone or view.status not in ("queued", "running"):
            return
        async with aclosing(store.claim_lock(command_id)) as locks:
            async for _session in locks:
                view = await self.load_command(command_id)
                if view is None:
                    return
                if view.undone or view.status not in ("queued", "running"):
                    return
                async with store.sessions.begin() as session:
                    row = await session.scalar(
                        select(CommandRow)
                        .options(load_only(CommandRow.id, CommandRow.status, CommandRow.undone_at))
                        .where(CommandRow.id == command_id)
                        .with_for_update()
                    )
                    if row is None or row.undone_at is not None or row.status not in ("queued", "running"):
                        return
                    row.status = "running"
                await store.append_event(Event(command_id=command_id, type="run.started"))
                try:
                    output = await self.runtime.bus.execute(view.submission)
                    updated = await store.command(command_id)
                    if updated.status == "running":
                        await store.finish(command_id, output)
                    if output.recovery_decision == "retry" and output.recovered_command_id:
                        with suppress(RedisError):
                            await self.queue.enqueue(output.recovered_command_id)
                except Exception as error:
                    logger.exception("Command %s failed", command_id)
                    # Preserve checkpoint and traces; no partial canonical story writes.
                    await store.fail(
                        command_id,
                        str(error)
                        if isinstance(error, DraftRevisionError)
                        else f"{type(error).__name__}: {error}",
                    )

    async def load_command(self, command_id: UUID) -> CommandView | None:
        try:
            if await self.runtime.store.command_lifecycle(command_id) is None:
                return None
            return await self.runtime.store.command(command_id, strict_checkpoint=True)
        except InvalidCheckpointError as error:
            logger.error("Command %s has an invalid checkpoint: %s", command_id, error.summary)
            await self.runtime.store.fail_invalid_checkpoint(command_id, error.summary)
            return None
        except ValueError:
            if await self.runtime.store.command_lifecycle(command_id) is None:
                return None
            raise

    async def run(self) -> None:
        recovery = asyncio.create_task(self.recover())
        try:
            while True:
                try:
                    command_id = await self.queue.receive()
                    if command_id is not None:
                        await self.process(command_id)
                        await self.queue.ack(command_id)
                except RedisError:
                    logger.exception("Queue unavailable; retrying")
                    await asyncio.sleep(2)
        finally:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery
            await self.queue.close()
            await self.runtime.close()
