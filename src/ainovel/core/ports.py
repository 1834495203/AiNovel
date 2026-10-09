from typing import Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from ainovel.core.discussion import DiscussionAction, DiscussionContext
from ainovel.core.domain import (
    ContextChunk,
    Extraction,
    PromptTask,
    PromptTemplateVersion,
    Revision,
    Story,
    Trace,
)

T = TypeVar("T", bound=BaseModel)


class LanguageModel(Protocol):
    async def generate(
        self,
        schema: type[T],
        task: PromptTask,
        context: BaseModel,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> T: ...

    async def choose_discussion_tool(
        self,
        context: DiscussionContext,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> DiscussionAction: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str], command_id: UUID) -> list[list[float]]: ...


class TraceSink(Protocol):
    async def record(self, command_id: UUID, trace: Trace) -> None: ...


class KnowledgeGraph(Protocol):
    """Future graph adapter: derive only from committed text; retain revision provenance."""

    async def prepare(self, story: Story, extraction: Extraction | None) -> None: ...
    async def reconcile(self, revision: Revision) -> None: ...
    async def retrieve(self, revision_id: UUID, query: str) -> list[ContextChunk]: ...
