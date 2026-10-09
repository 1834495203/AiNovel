"""Database-backed implementation of the model trace sink."""

from uuid import UUID

from ainovel.core.domain import Event, Trace
from ainovel.mapper.store import Store


class DatabaseTraceSink:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def record(self, command_id: UUID, trace: Trace) -> None:
        await self.store.append_event(
            Event(
                command_id=command_id,
                actor="ai",
                type="llm.completed" if trace.error is None else "llm.failed",
                trace=trace,
            )
        )
