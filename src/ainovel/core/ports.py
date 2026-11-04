from typing import Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from ainovel.core.agent import AgentAction, AgentContext
from ainovel.core.domain import (
    ContextChunk,
    Extraction,
    PromptTemplateVersion,
    Revision,
    Story,
    Trace,
)

T = TypeVar("T", bound=BaseModel)


class LanguageModel(Protocol):
    async def choose_agent_tool(
        self,
        context: AgentContext,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> AgentAction: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str], command_id: UUID) -> list[list[float]]: ...


class TraceSink(Protocol):
    async def record(self, command_id: UUID, trace: Trace) -> None: ...


class KnowledgeGraph(Protocol):
    """Future graph adapter: derive only from committed text; retain revision provenance."""

    async def prepare(self, story: Story, extraction: Extraction | None) -> None: ...
    async def reconcile(self, revision: Revision) -> None: ...
    async def retrieve(self, revision_id: UUID, query: str) -> list[ContextChunk]: ...
