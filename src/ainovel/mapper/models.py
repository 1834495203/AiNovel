"""Database table mappings; no application orchestration."""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ainovel.core.domain import (
    CommandOutput,
    CommandSubmission,
    ContextChunk,
    Event,
    OperationMemento,
    PromptTemplateVersion,
    Revision,
    SetupDraft,
    WritingState,
)
from ainovel.core.field_history import FieldChangeEvent, FieldSnapshot
from ainovel.core.fields import FieldTemplate, FieldTemplateSet
from ainovel.mapper.types import ModelJSON
from ainovel.utils.time import now


class Base(DeclarativeBase):
    pass


class StoryRow(Base):
    __tablename__ = "stories"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    head_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(200))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RevisionRow(Base):
    __tablename__ = "revisions"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    story_id: Mapped[UUID] = mapped_column(index=True)
    payload: Mapped[Revision] = mapped_column(ModelJSON(Revision))


class FieldChangeRow(Base):
    __tablename__ = "field_changes"
    revision_id: Mapped[UUID] = mapped_column(primary_key=True)
    story_id: Mapped[UUID] = mapped_column(index=True)
    parent_id: Mapped[UUID | None] = mapped_column(index=True)
    payload: Mapped[FieldChangeEvent] = mapped_column(ModelJSON(FieldChangeEvent))


class FieldSnapshotRow(Base):
    __tablename__ = "field_snapshots"
    revision_id: Mapped[UUID] = mapped_column(primary_key=True)
    story_id: Mapped[UUID] = mapped_column(index=True)
    payload: Mapped[FieldSnapshot] = mapped_column(ModelJSON(FieldSnapshot))


class FieldTemplateRow(Base):
    __tablename__ = "field_templates"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    command_id: Mapped[UUID] = mapped_column(index=True, unique=True)
    payload: Mapped[FieldTemplate] = mapped_column(ModelJSON(FieldTemplate))


class CommandRow(Base):
    __tablename__ = "commands"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    story_id: Mapped[UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")
    payload: Mapped[CommandSubmission] = mapped_column(ModelJSON(CommandSubmission))
    output: Mapped[CommandOutput] = mapped_column(ModelJSON(CommandOutput), default=CommandOutput)
    checkpoint: Mapped[WritingState | None] = mapped_column(ModelJSON(WritingState))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FieldTemplateSetRow(Base):
    __tablename__ = "field_template_sets"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    command_id: Mapped[UUID] = mapped_column(index=True, unique=True)
    payload: Mapped[FieldTemplateSet] = mapped_column(ModelJSON(FieldTemplateSet))


class OperationRow(Base):
    __tablename__ = "operations"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    scope_id: Mapped[UUID] = mapped_column(index=True)
    command_id: Mapped[UUID] = mapped_column(index=True)
    before: Mapped[OperationMemento] = mapped_column(ModelJSON(OperationMemento))
    undone_by: Mapped[UUID | None] = mapped_column(index=True)


class SetupDraftRow(Base):
    __tablename__ = "setup_drafts"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    payload: Mapped[SetupDraft] = mapped_column(ModelJSON(SetupDraft))


class ResourceDeletionRow(Base):
    """Content-free lifecycle barriers prevent stale requests resurrecting deleted data."""

    __tablename__ = "resource_deletions"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20))


class PromptTemplateVersionRow(Base):
    __tablename__ = "prompt_template_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[UUID] = mapped_column(index=True)
    task: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[PromptTemplateVersion] = mapped_column(ModelJSON(PromptTemplateVersion))


class PromptTemplateBindingRow(Base):
    __tablename__ = "prompt_template_bindings"
    __table_args__ = (UniqueConstraint("scope_id", "task"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope_id: Mapped[UUID] = mapped_column(index=True)
    task: Mapped[str] = mapped_column(String(30))
    version_id: Mapped[UUID] = mapped_column(index=True)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    command_id: Mapped[UUID] = mapped_column(index=True)
    payload: Mapped[Event] = mapped_column(ModelJSON(Event))


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("revision_id", "chunk_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    chunk_id: Mapped[UUID] = mapped_column(index=True)
    revision_id: Mapped[UUID] = mapped_column(index=True)
    chapter_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[ContextChunk] = mapped_column(ModelJSON(ContextChunk))
    embedding: Mapped[list[float]] = mapped_column(Vector())
