"""Atomic permanent erasure, including command audit and official graph checkpoints."""

from uuid import UUID

from sqlalchemy import cast, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ainovel.core.domain import PurgeResult
from ainovel.core.errors import ConflictError
from ainovel.mapper.models import (
    ChunkRow,
    CommandRow,
    EventRow,
    FieldChangeRow,
    FieldSnapshotRow,
    OperationRow,
    PromptTemplateBindingRow,
    PromptTemplateVersionRow,
    ResourceDeletionRow,
    RevisionRow,
    SetupDraftRow,
    StoryRow,
)
from ainovel.mapper.store import Store


def json_text(session: AsyncSession, value, key: str):
    return (cast(value, JSONB)[key].astext
            if session.bind.dialect.name == "postgresql"
            else func.json_extract(value, f"$.{key}"))


def session_condition(session: AsyncSession, session_id: UUID):
    wire = json_text(session, CommandRow.payload, "session_id")
    return CommandRow.story_id.is_(None) & (wire == str(session_id))


async def purge(store: Store, resource_id: UUID, *, draft: bool) -> PurgeResult:
    """Only trashed resources can be erased. UUID-only barriers block stale replay.

    This intentionally creates no command or undo record: those records are being
    erased. All PostgreSQL content and checkpoints are removed in one transaction.
    Worker leases are held until commit, so a graph cannot recreate its checkpoints.
    """
    async with store.sessions.begin() as session:
        # Discover only typed command metadata, never deserialize historical checkpoints.
        metadata = select(
            CommandRow.id, CommandRow.story_id, CommandRow.payload, CommandRow.output,
        )
        output_story = json_text(session, CommandRow.output, "story_id")
        creation_session = json_text(session, CommandRow.payload, "session_id")
        condition = session_condition(session, resource_id) if draft else (
            (CommandRow.story_id == resource_id) | (output_story == str(resource_id))
        )
        rows = (await session.execute(metadata.where(condition))).all()
        story_ids: set[UUID] = set() if draft else {resource_id}
        draft_ids: set[UUID] = {resource_id} if draft else set()
        if not draft:
            for row in rows:
                if row.payload.payload.kind == "create_story" and row.output.story_id == resource_id:
                    if row.payload.session_id:
                        draft_ids.add(row.payload.session_id)
        # Include hidden stories left by undoing creation of this draft, but never
        # erase an unrelated active story sharing a legacy creation session.
        for row in rows:
            if row.payload.session_id in draft_ids and row.payload.payload.kind == "create_story" and row.output.story_id:
                story_ids.add(row.output.story_id)
        scopes = story_ids | draft_ids
        for scope in [*sorted(story_ids, key=str), *sorted(draft_ids, key=str)]:
            await store.lock_operation_scope(session, scope)
        barrier = await session.get(ResourceDeletionRow, resource_id)
        if barrier and barrier.status == "purged":
            return PurgeResult(cache_ids=[resource_id])
        if draft:
            if barrier is None or barrier.status != "deleted":
                raise ConflictError("请先将创作草稿移入回收站")
        for story_id in story_ids:
            story = await session.get(StoryRow, story_id, with_for_update=True)
            if story is None:
                if not draft and story_id == resource_id:
                    raise ValueError("小说不存在")
            elif story.deleted_at is None:
                raise ConflictError("请先将关联小说移入回收站，再永久删除")
        # Re-read after scope locks: queued submissions cannot slip past the erasure.
        rows = (await session.execute(metadata.where(
            CommandRow.story_id.in_(story_ids) | output_story.in_([str(value) for value in story_ids])
            | (CommandRow.story_id.is_(None) & creation_session.in_([str(value) for value in draft_ids]))
        ))).all()
        if any(row.payload.session_id in draft_ids and row.payload.payload.kind == "create_story"
               and row.output.story_id and row.output.story_id not in story_ids for row in rows):
            raise ConflictError("创建会话已产生新的小说，请刷新回收站后重试")
        command_ids = {row.id for row in rows if (
            row.story_id in story_ids or row.output.story_id in story_ids
            or (row.story_id is None and row.payload.session_id in draft_ids)
        )}
        cache_ids = scopes | command_ids
        cache_ids.update((await session.scalars(select(RevisionRow.id).where(RevisionRow.story_id.in_(story_ids)))).all())
        for row in rows:
            if row.id in command_ids:
                for session_id in (row.payload.session_id, getattr(row.payload.payload, "session_id", None), row.output.chat_session_id):
                    if session_id:
                        cache_ids.add(session_id)
        # A nonblocking lease check also covers a cancelled worker finishing an HTTP
        # model call. Never delete checkpoints while that process can still write.
        if store.engine.dialect.name == "postgresql":
            for command_id in sorted(command_ids, key=str):
                acquired = await session.scalar(select(func.pg_try_advisory_xact_lock(command_id.int % (2**63 - 1))))
                if not acquired:
                    raise ConflictError("后台任务仍在结束处理中，请稍后重试永久删除")
        revision_ids = select(RevisionRow.id).where(RevisionRow.story_id.in_(story_ids))
        await session.execute(delete(ChunkRow).where(ChunkRow.revision_id.in_(revision_ids)))
        for model in (FieldChangeRow, FieldSnapshotRow, RevisionRow):
            await session.execute(delete(model).where(model.story_id.in_(story_ids)))
        for model in (PromptTemplateBindingRow, PromptTemplateVersionRow):
            await session.execute(delete(model).where(model.scope_id.in_(scopes)))
        await session.execute(delete(OperationRow).where(
            OperationRow.scope_id.in_(scopes) | OperationRow.command_id.in_(command_ids)
        ))
        await session.execute(delete(EventRow).where(EventRow.command_id.in_(command_ids)))
        await session.execute(delete(CommandRow).where(CommandRow.id.in_(command_ids)))
        await session.execute(delete(SetupDraftRow).where(SetupDraftRow.id.in_(draft_ids)))
        await session.execute(delete(StoryRow).where(StoryRow.id.in_(story_ids)))
        # Keep no title, prose, prompts, history or personal metadata, only opaque
        # IDs needed to reject retries from old tabs and queue deliveries.
        for identifier in scopes | command_ids:
            row = await session.get(ResourceDeletionRow, identifier)
            if row is None:
                session.add(ResourceDeletionRow(id=identifier, status="purged"))
            else:
                row.status = "purged"
        return PurgeResult(command_ids=list(command_ids), cache_ids=list(cache_ids))
