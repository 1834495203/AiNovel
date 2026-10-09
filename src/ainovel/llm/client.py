"""Compatibility name for older integrations. Runtime uses LLMService directly."""

import httpx

from ainovel.llm.service import LLMService
from ainovel.llm.transport import OpenAICompatibleTransport


class OpenAIModel(LLMService):
    """Legacy import and HTTP test seam; contains no model execution logic."""

    @property
    def client(self) -> httpx.AsyncClient:
        transport = self.gateway.transport
        if not isinstance(transport, OpenAICompatibleTransport):
            raise TypeError("自定义 transport 不提供 HTTP 客户端")
        return transport.client

    @client.setter
    def client(self, value: httpx.AsyncClient) -> None:
        transport = self.gateway.transport
        if not isinstance(transport, OpenAICompatibleTransport):
            raise TypeError("自定义 transport 不提供 HTTP 客户端")
        transport.client = value
