from uuid import UUID

from ainovel.core.domain import ContextChunk, Extraction, Revision, Story


class NullKnowledgeGraph:
    async def prepare(self, story: Story, extraction: Extraction | None) -> None:
        return None

    async def reconcile(self, revision: Revision) -> None:
        return None

    async def retrieve(self, revision_id: UUID, query: str) -> list[ContextChunk]:
        return []
