"""Composition root: adapters can be replaced without changing commands or domain models."""

from ainovel.core.config import Settings
from ainovel.core.ports import Embedder, LanguageModel
from ainovel.infrastructure.graph import NullKnowledgeGraph
from ainovel.infrastructure.tracing import DatabaseTraceSink
from ainovel.llm.embeddings import HashEmbedder
from ainovel.llm.mock import MockModel
from ainovel.llm.service import LLMService
from ainovel.mapper.store import Store
from ainovel.service.commands import CommandBus, Services
from ainovel.service.retrieval import RetrievalService
from ainovel.utils.chunking import ParagraphChunker


class Runtime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.store = Store(self.settings)
        self.sink = DatabaseTraceSink(self.store)
        self.model: LanguageModel
        if self.settings.llm_provider == "openai":
            self.model = LLMService(self.settings, self.sink)
        else:
            self.model = MockModel(self.sink)
        self.embedding_model: LLMService | None = None
        embedder: Embedder
        if self.settings.llm_provider != "mock" and self.settings.embedding_provider == "openai":
            embedding_settings = self.settings.model_copy()
            embedding_settings.llm_base_url = self.settings.embedding_base_url
            embedding_settings.llm_api_key = self.settings.embedding_api_key
            self.embedding_model = LLMService(embedding_settings, self.sink)
            embedder = self.embedding_model
        else:
            embedder = HashEmbedder(self.settings.embedding_dimensions)
        self.retrieval = RetrievalService(self.store, embedder, ParagraphChunker(self.settings.chunk_target))
        self.services = Services(self.store, self.model, self.retrieval, NullKnowledgeGraph(), self.settings)
        self.bus = CommandBus(self.services)

    async def close(self) -> None:
        if isinstance(self.model, LLMService):
            await self.model.close()
        if self.embedding_model is not None:
            await self.embedding_model.close()
        await self.store.close()
