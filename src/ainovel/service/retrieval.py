"""Paragraph-safe chunks; two semantic routes; character recall only from known experiences."""

from uuid import UUID

import jieba
from rank_bm25 import BM25Okapi

from ainovel.core.characters import Experience, Snapshot
from ainovel.core.domain import ContextChunk, Story
from ainovel.core.ports import Embedder
from ainovel.mapper.chunks import ChunkMapper
from ainovel.mapper.store import Store
from ainovel.utils.chunking import ParagraphChunker


class RetrievalService:
    def __init__(self, store: Store, embedder: Embedder, chunker: ParagraphChunker) -> None:
        self.store = store
        self.chunks = ChunkMapper(store)
        self.embedder = embedder
        self.chunker = chunker

    async def index(self, story: Story, command_id: UUID) -> tuple[list[ContextChunk], list[list[float]]]:
        chunks = self.chunker.story_chunks(story)
        embeddings = (
            await self.embedder.embed([chunk.content for chunk in chunks], command_id) if chunks else []
        )
        return chunks, embeddings

    async def narrative(self, revision_id: UUID, query: str, command_id: UUID) -> list[ContextChunk]:
        vector = (await self.embedder.embed([query], command_id))[0]
        candidates = await self.chunks.recall(revision_id, vector)
        merged: list[ContextChunk] = []
        seen: set[UUID] = set()
        for chunk in candidates.paragraphs + candidates.global_hits + candidates.chapters:
            if chunk.id not in seen:
                seen.add(chunk.id)
                merged.append(chunk)
        # First choose by relevance, then present evidence in narrative time order.
        return sorted(merged, key=lambda chunk: (chunk.ordinal, chunk.kind))

    def character(self, snapshot: Snapshot, query: str, names: list[str]) -> list[Experience]:
        memories = snapshot.experiences
        if not memories:
            return []
        corpus = [
            jieba.lcut(
                " ".join([memory.event, *[entry.text() for entry in memory.entries], *memory.keywords])
            )
            or ["空"]
            for memory in memories
        ]
        scores = BM25Okapi(corpus).get_scores(jieba.lcut(" ".join([query, *names])))
        selected = sorted(range(len(memories)), key=lambda index: float(scores[index]), reverse=True)[:8]
        return [memories[index] for index in sorted(selected)]
