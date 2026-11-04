"""Unified public model service; strategies share one gateway and transport."""

from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from ainovel.core.agent import AgentAction, AgentContext
from ainovel.core.config import Settings
from ainovel.core.domain import PromptTask, PromptTemplateVersion
from ainovel.core.ports import TraceSink
from ainovel.llm.agent import AgentResultDecoder, build_agent_request
from ainovel.llm.gateway import ModelGateway
from ainovel.llm.structured import StructuredResultDecoder, build_structured_request
from ainovel.llm.transport import ModelTransport, OpenAICompatibleTransport

T = TypeVar("T", bound=BaseModel)


class LLMService:
    def __init__(
        self, settings: Settings, sink: TraceSink, *, transport: ModelTransport | None = None
    ) -> None:
        self.settings = settings
        self.gateway = ModelGateway(
            settings, sink, transport if transport is not None else OpenAICompatibleTransport(settings)
        )

    async def generate(
        self,
        schema: type[T],
        task: PromptTask,
        context: BaseModel,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> T:
        request = build_structured_request(self.settings, schema, task, context, prompt_template)
        return await self.gateway.complete(request, StructuredResultDecoder(schema), command_id)

    async def choose_agent_tool(
        self,
        context: AgentContext,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> AgentAction:
        request = build_agent_request(self.settings, context, prompt_template)
        return await self.gateway.complete(request, AgentResultDecoder(task=getattr(context,"task","scene_planning" if context.planning_mode else "discussion")), command_id)

    async def embed(self, texts: list[str], command_id: UUID) -> list[list[float]]:
        return await self.gateway.embed(texts, command_id)

    async def close(self) -> None:
        await self.gateway.close()
