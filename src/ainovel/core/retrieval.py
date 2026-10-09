"""Typed results at the vector-query boundary; ordering is an application decision."""

from pydantic import Field

from ainovel.core.domain import ContextChunk, Model


class RecallCandidates(Model):
    chapters: list[ContextChunk] = Field(default_factory=list)
    paragraphs: list[ContextChunk] = Field(default_factory=list)
    global_hits: list[ContextChunk] = Field(default_factory=list)
