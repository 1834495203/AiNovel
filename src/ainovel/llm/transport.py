"""Provider HTTP boundary. Business code never constructs HTTP requests."""

from typing import Literal, Protocol

import httpx
from pydantic import BaseModel

from ainovel.core.config import Settings
from ainovel.llm.schemas import ChatBody, DeepSeekChatBody, ThinkingMode, ToolChoice

Endpoint = Literal["chat/completions", "embeddings"]


class ModelTransport(Protocol):
    def prepare_chat(self, body: ChatBody) -> ChatBody: ...
    async def post(self, endpoint: Endpoint, body: BaseModel) -> str: ...
    async def close(self) -> None: ...


class OpenAICompatibleTransport:
    """OpenAI-compatible HTTP adapter, also used by DeepSeek."""

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key.get_secret_value():
            raise ValueError("真实模型需要 LLM_API_KEY 或 OPENAI_API_KEY")
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/") + "/",
            timeout=settings.model_timeout,
            headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
        )

    def prepare_chat(self, body: ChatBody) -> ChatBody:
        if not (body.model.startswith("deepseek-") or "deepseek" in self.settings.llm_base_url.lower()):
            return body
        if isinstance(body.tool_choice, ToolChoice) and self.settings.llm_thinking != "disabled":
            raise ValueError("DeepSeek 强制工具调用需要 LLM_THINKING=disabled")
        return DeepSeekChatBody(
            model=body.model,
            messages=body.messages,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            tools=body.tools,
            tool_choice=body.tool_choice,
            thinking=ThinkingMode(type="disabled"),
        )

    async def post(self, endpoint: Endpoint, body: BaseModel) -> str:
        response = await self.client.post(
            endpoint, content=body.model_dump_json(exclude_none=True), headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.text

    async def close(self) -> None:
        await self.client.aclose()
