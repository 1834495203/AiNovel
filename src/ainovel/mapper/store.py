"""SQLAlchemy adapter. Pydantic JSON is serialized only at the database boundary."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import load_only

from ainovel.core.config import Settings
from ainovel.core.discussion import DiscussionResult
from ainovel.core.domain import (
    CommandLifecycle,
    CommandMemento,
    CommandOutput,
    CommandProjection,
    CommandSubmission,
    CommandView,
    ContextChunk,
    Event,
    EventView,
    OperationMemento,
    PromptTask,
    PromptTemplateBindingMemento,
    PromptTemplateBundle,
    PromptTemplateVersion,
    Revision,
    SavePromptTemplateRequest,
    Story,
    StorySummary,
    StoryView,
    Trace,
    WritingDraft,
    WritingState,
)
from ainovel.core.errors import ConflictError, InvalidCheckpointError, StoryUnavailableError
from ainovel.mapper.models import (
    Base,
    ChunkRow,
    CommandRow,
    EventRow,
    OperationRow,
    PromptTemplateBindingRow,
    PromptTemplateVersionRow,
    ResourceDeletionRow,
    RevisionRow,
    StoryRow,
)
from ainovel.utils.time import now


class Store:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_async_engine(settings.database_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            if self.engine.dialect.name == "postgresql":
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
            if self.engine.dialect.name == "postgresql":
                # Idempotent upgrade for MVP databases created before command timestamps were added.
                await connection.execute(
                    text(
                        "ALTER TABLE commands ADD COLUMN IF NOT EXISTS created_at "
                        "TIMESTAMPTZ NOT NULL DEFAULT now()"
                    )
                )
                await connection.execute(
                    text("ALTER TABLE stories ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
                )
                await connection.execute(
                    text("ALTER TABLE commands ADD COLUMN IF NOT EXISTS undone_at TIMESTAMPTZ")
                )

    @staticmethod
    async def active_story(session: AsyncSession, story_id: UUID, locked: bool = False) -> StoryRow:
        query = select(StoryRow).where(StoryRow.id == story_id)
        if locked:
            query = query.with_for_update()
        row = await session.scalar(query)
        if row is None:
            raise ValueError("小说不存在")
        if row.deleted_at is not None:
            raise StoryUnavailableError("小说已删除，请先从回收站恢复")
        return row

    async def close(self) -> None:
        await self.engine.dispose()

    async def health(self) -> bool:
        try:
            async with self.sessions() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @staticmethod
    def view(row: CommandRow) -> CommandView:
        return CommandView(
            submission=row.payload,
            status=row.status,
            output=row.output,
            error=row.error,
            created_at=row.created_at,
            undone=row.undone_at is not None,
            scene_id=row.checkpoint.scene_id if row.checkpoint else None,
        )

    @staticmethod
    def projected_view(row: CommandProjection) -> CommandView:
        scene_id = None
        if row.checkpoint_json is not None:
            try:
                scene_id = WritingState.model_validate_json(row.checkpoint_json).scene_id
            except ValidationError:
                # Keep malformed historical checkpoint JSON intact and still show
                # the typed command metadata in the UI.
                pass
        return CommandView(
            submission=row.submission,
            status=row.status,
            output=row.output,
            error=row.error,
            created_at=row.created_at,
            undone=row.undone_at is not None,
            scene_id=scene_id,
        )

    @staticmethod
    def command_projection_query():
        return select(
            CommandRow.id,
            CommandRow.status,
            CommandRow.payload.label("submission"),
            CommandRow.output,
            CommandRow.error,
            CommandRow.created_at,
            CommandRow.undone_at,
            cast(CommandRow.__table__.c.checkpoint, Text).label("checkpoint_json"),
        )

    @staticmethod
    def command_projection(row: object) -> CommandProjection:
        return CommandProjection(
            id=row.id,
            status=row.status,
            submission=row.submission,
            output=row.output,
            error=row.error,
            created_at=row.created_at,
            undone_at=row.undone_at,
            checkpoint_json=row.checkpoint_json,
        )

    async def submit(self, submission: CommandSubmission) -> CommandView:
        async with self.sessions.begin() as session:
            scope = submission.story_id or submission.session_id or submission.id
            await self.lock_operation_scope(session, scope)
            await self.check_resource(session, submission.id)
            await self.check_resource(session, scope, allow_deleted=submission.payload.kind in (
                "set_setup_draft_deleted", "undo", "recover_command",
            ))
            existing = await session.get(CommandRow, submission.id)
            if existing is not None:
                if existing.payload != submission:
                    raise ConflictError("命令 ID 已被不同请求使用")
                return self.view(existing)
            if submission.story_id is not None:
                if submission.payload.kind not in ("restore_story", "delete_story", "undo"):
                    await self.active_story(session, submission.story_id, locked=True)
                revision = await session.get(RevisionRow, submission.base_revision_id)
                if revision is None or revision.story_id != submission.story_id:
                    raise ValueError("版本不属于这部小说")
            row = CommandRow(
                id=submission.id,
                story_id=submission.story_id,
                payload=submission,
                status="queued",
                output=CommandOutput(),
                created_at=now(),
            )
            session.add(row)
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, actor="user", type="command.accepted"),
                )
            )
            return self.view(row)

    async def command(self, command_id: UUID, *, strict_checkpoint: bool = False) -> CommandView:
        async with self.sessions() as session:
            row = (
                await session.execute(self.command_projection_query().where(CommandRow.id == command_id))
            ).one_or_none()
            if row is None:
                raise ValueError("命令不存在")
            projection = self.command_projection(row)
            if strict_checkpoint and projection.checkpoint_json is not None:
                try:
                    WritingState.model_validate_json(projection.checkpoint_json)
                except ValidationError as error:
                    raise InvalidCheckpointError(command_id, error) from error
            return self.projected_view(projection)

    async def command_lifecycle(self, command_id: UUID) -> CommandLifecycle | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(CommandRow.id, CommandRow.status, CommandRow.undone_at).where(
                        CommandRow.id == command_id
                    )
                )
            ).one_or_none()
            return CommandLifecycle(id=row.id, status=row.status, undone_at=row.undone_at) if row else None

    async def checkpoint(self, command_id: UUID) -> WritingState | None:
        async with self.sessions() as session:
            row = await session.get(CommandRow, command_id)
            if row is None:
                raise ValueError("命令不存在")
            return row.checkpoint

    async def save_checkpoint(self, state: WritingState) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(CommandRow, state.execution_id)
            if row is None:
                raise ValueError("命令不存在")
            if row.status != "running":
                raise ConflictError("任务已停止，不能继续保存生成状态")
            row.checkpoint = state

    async def finish(self, command_id: UUID, output: CommandOutput, status: str = "completed") -> None:
        async with self.sessions.begin() as session:
            row = await session.get(CommandRow, command_id)
            if row is None:
                raise ValueError("命令不存在")
            if row.status != "running":
                raise ConflictError("任务已停止，不能更改执行结果")
            if row.payload.payload.kind != "undo":
                await self.record_operation(session, row.payload)
                # Deletion/undo may have finished while this command was waiting for the scope lock.
                await session.refresh(row, with_for_update=True)
                if row.status != "running" or row.undone_at is not None:
                    raise ConflictError("任务已停止，不能更改执行结果")
            row.output = output
            row.status = status
            row.error = None
            session.add(
                EventRow(
                    command_id=command_id,
                    payload=Event(command_id=command_id, type=f"run.{status}", output=output),
                )
            )

    async def fail(self, command_id: UUID, error: str) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(CommandRow, command_id)
            if row is None:
                return
            scope = row.payload.story_id or row.payload.session_id or row.id
            await self.lock_operation_scope(session, scope)
            await session.refresh(row, with_for_update=True)
            if row.status not in ("queued", "running") or row.undone_at is not None:
                return
            if row.payload.payload.kind not in ("undo", "recover_command", "save_setup_draft"):
                await self.record_operation(session, row.payload)
            row.status = "failed"
            row.error = error
            session.add(
                EventRow(
                    command_id=command_id,
                    payload=Event(
                        command_id=command_id, type="run.failed", output=CommandOutput(message=error)
                    ),
                )
            )

    async def fail_invalid_checkpoint(self, command_id: UUID, error: str) -> None:
        """Fail a command without deserializing, clearing, or rewriting its bad checkpoint."""
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(CommandRow)
                .options(
                    load_only(
                        CommandRow.id,
                        CommandRow.status,
                        CommandRow.payload,
                        CommandRow.undone_at,
                    )
                )
                .where(CommandRow.id == command_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("命令不存在")
            if row.status not in ("queued", "running") or row.undone_at is not None:
                return
            await self.record_operation(session, row.payload)
            message = f"CheckpointValidationError: command {command_id}: {error}"
            row.status = "failed"
            row.error = message
            session.add(
                EventRow(
                    command_id=command_id,
                    payload=Event(
                        command_id=command_id, type="run.failed", output=CommandOutput(message=message)
                    ),
                )
            )

    async def append_event(self, event: Event) -> None:
        async with self.sessions.begin() as session:
            if await session.get(CommandRow, event.command_id, with_for_update=True) is None:
                return
            session.add(EventRow(command_id=event.command_id, payload=event))

    async def pin_prompt_templates(
        self, command_id: UUID, templates: list[PromptTemplateVersion]
    ) -> list[PromptTemplateVersion]:
        """Persist a command's resolved prompt snapshot once; retries always reuse it."""
        from ainovel.service.prompt_templates import freeze_prompt_template

        async with self.sessions.begin() as session:
            await session.get(CommandRow, command_id, with_for_update=True)
            rows = await session.scalars(
                select(EventRow)
                .where(EventRow.command_id == command_id)
                .order_by(EventRow.id)
            )
            for row in rows:
                event = row.payload
                if event.type == "prompt_templates.pinned" and event.trace is not None:
                    bundle = PromptTemplateBundle.model_validate_json(event.trace.structured_output)
                    return bundle.templates
            bundle = PromptTemplateBundle(templates=[freeze_prompt_template(item) for item in templates])
            session.add(
                EventRow(
                    command_id=command_id,
                    payload=Event(
                        command_id=command_id,
                        actor="system",
                        type="prompt_templates.pinned",
                        trace=Trace(
                            node="prompt_templates",
                            summary="固定本命令使用的提示词来源",
                            structured_output=bundle.model_dump_json(),
                        ),
                    ),
                )
            )
            return bundle.templates

    async def events(self, command_id: UUID, after: int = 0) -> list[EventView]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(EventRow)
                    .where(EventRow.command_id == command_id, EventRow.id > after)
                    .order_by(EventRow.id)
                    .limit(100)
                )
            ).all()
            return [EventView(id=row.id, event=row.payload) for row in rows]

    async def prompt_template_versions(self, scope_id: UUID) -> list[PromptTemplateVersion]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(PromptTemplateVersionRow)
                .where(PromptTemplateVersionRow.scope_id == scope_id)
                .order_by(PromptTemplateVersionRow.task, PromptTemplateVersionRow.version)
            )
            return [row.payload for row in rows]

    async def prompt_template_version(self, version_id: UUID) -> PromptTemplateVersion:
        async with self.sessions() as session:
            row = await session.get(PromptTemplateVersionRow, version_id)
            if row is None:
                raise ValueError("提示词模板版本不存在")
            return row.payload

    async def active_prompt_template(self, scope_id: UUID, task: PromptTask) -> PromptTemplateVersion | None:
        async with self.sessions() as session:
            binding = await session.scalar(
                select(PromptTemplateBindingRow).where(
                    PromptTemplateBindingRow.scope_id == scope_id,
                    PromptTemplateBindingRow.task == task,
                )
            )
            if binding is None:
                return None
            row = await session.get(PromptTemplateVersionRow, binding.version_id)
            return row.payload if row is not None else None

    async def save_prompt_template(
        self, submission: CommandSubmission, request: SavePromptTemplateRequest, scope_id: UUID
    ) -> PromptTemplateVersion | None:
        async with self.sessions.begin() as session:
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running":
                raise ConflictError("模板命令已停止")
            await self.record_operation(session, submission)
            binding = await session.scalar(
                select(PromptTemplateBindingRow)
                .where(
                    PromptTemplateBindingRow.scope_id == scope_id,
                    PromptTemplateBindingRow.task == request.task,
                )
                .with_for_update()
            )
            if request.action == "reset":
                if binding is not None:
                    await session.delete(binding)
                return None
            latest = await session.scalar(
                select(func.max(PromptTemplateVersionRow.version)).where(
                    PromptTemplateVersionRow.scope_id == scope_id,
                    PromptTemplateVersionRow.task == request.task,
                )
            )
            from ainovel.core.prompting import (
                PROMPT_CONTEXT_VARIABLES,
                PromptRuntimeContext,
                render_prompt_source,
            )
            from ainovel.service.prompt_templates import default_template

            metadata = default_template(request.task)
            if request.source is not None:
                if not request.source.strip():
                    raise ValueError("完整提示词模板不能为空")
                rendered = render_prompt_source(request.task, request.source, PromptRuntimeContext())
                source = request.source
                source_format = "jinja2"
                source_hash = rendered.source_hash
                context_variables = rendered.context_variables
                context_schema = rendered.context_schema
            else:
                source = ""
                source_format = "legacy_guidance"
                source_hash = metadata.source_hash
                context_variables = PROMPT_CONTEXT_VARIABLES
                context_schema = metadata.context_schema
            from ainovel.core.context_layout import default_context_source
            render_prompt_source(request.task, request.context_source if request.context_source is not None else default_context_source(), PromptRuntimeContext())
            version = PromptTemplateVersion(
                id=uuid4(),
                scope=request.scope,
                scope_id=scope_id,
                task=request.task,
                name=request.name,
                version=(latest or 0) + 1,
                context_source=request.context_source if request.context_source is not None else metadata.context_source,
                guidance=request.guidance.strip() if source_format == "legacy_guidance" else "",
                source_format=source_format,
                source=source,
                editor_source=source or metadata.source,
                template_path=metadata.template_path,
                source_hash=source_hash,
                context_variables=context_variables,
                context_schema=context_schema,
            )
            session.add(
                PromptTemplateVersionRow(
                    id=version.id,
                    scope_id=scope_id,
                    task=request.task,
                    version=version.version,
                    payload=version,
                )
            )
            if binding is None:
                session.add(
                    PromptTemplateBindingRow(
                        scope_id=scope_id, task=request.task, version_id=version.id
                    )
                )
            else:
                binding.version_id = version.id
            return version

    async def pending(self) -> list[UUID]:
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(CommandRow.id).where(CommandRow.status.in_(("queued", "running"))).limit(100)
                    )
                ).all()
            )

    async def revision(self, revision_id: UUID) -> Revision:
        async with self.sessions() as session:
            row = await session.get(RevisionRow, revision_id)
            if row is None:
                raise ValueError("版本不存在")
            await self.active_story(session, row.story_id)
            from ainovel.mapper.field_history import restore

            return row.payload.model_copy(update={"story": await restore(session, revision_id, row.story_id)})

    async def head(self, story_id: UUID) -> StoryView:
        async with self.sessions() as session:
            row = await self.active_story(session, story_id)
            revision = await session.get(RevisionRow, row.head_id)
            if revision is None:
                raise ValueError("小说版本丢失")
            from ainovel.mapper.field_history import restore

            return StoryView(revision_id=row.head_id, story=await restore(session, row.head_id, story_id))

    async def stories(self, deleted: bool = False) -> list[StorySummary]:
        async with self.sessions() as session:
            query = select(StoryRow, RevisionRow).join(RevisionRow, RevisionRow.id == StoryRow.head_id)
            query = query.where(
                StoryRow.deleted_at.is_not(None) if deleted else StoryRow.deleted_at.is_(None)
            )
            rows = (await session.execute(query.order_by(StoryRow.title))).all()
            return [
                StorySummary(
                    id=story.id,
                    title=story.title,
                    revision_id=story.head_id,
                    scene_count=len(revision.payload.story.scenes),
                    deleted_at=story.deleted_at,
                )
                for story, revision in rows
            ]

    async def revisions(self, story_id: UUID) -> list[Revision]:
        async with self.sessions() as session:
            await self.active_story(session, story_id)
            rows = (await session.scalars(select(RevisionRow).where(RevisionRow.story_id == story_id))).all()
            return sorted((row.payload for row in rows), key=lambda revision: revision.created_at)

    async def commit(
        self,
        submission: CommandSubmission,
        story: Story,
        reason: str,
        chunks: list[ContextChunk],
        embeddings: list[list[float]],
        consumed_proposal_id: UUID | None = None,
        confirmed_generation_id: UUID | None = None,
        writing_draft: WritingDraft | None = None,
        discussion: "DiscussionResult | None" = None,
    ) -> CommandOutput:
        if len(chunks) != len(embeddings):
            raise ValueError("chunk/embedding length mismatch")
        revision = Revision(
            story_id=story.id,
            parent_id=submission.base_revision_id,
            command_id=submission.id,
            reason=reason,
            story=story,
        )
        output = CommandOutput(
            story_id=story.id,
            revision_id=revision.id,
            message=reason,
            writing_draft=writing_draft,
            discussion=discussion.model_copy(update={"updated_revision_id": revision.id})
            if discussion
            else None,
        )
        async with self.sessions.begin() as session:
            await self.record_operation(
                session,
                submission,
                story_id=story.id,
                related_ids=[
                    identifier for identifier in (consumed_proposal_id, confirmed_generation_id) if identifier
                ],
            )
            row = await session.scalar(select(StoryRow).where(StoryRow.id == story.id).with_for_update())
            if row is None:
                if submission.base_revision_id is not None:
                    raise ConflictError("小说不存在")
                session.add(StoryRow(id=story.id, head_id=revision.id, title=story.title))
            else:
                if row.deleted_at is not None:
                    raise StoryUnavailableError("小说已删除，生成结果不会重新写入")
                if row.head_id != submission.base_revision_id:
                    raise ConflictError("小说已发生变化，请刷新后重新提交；本次输出未覆盖新版本")
                row.head_id = revision.id
                row.title = story.title
            if consumed_proposal_id is not None:
                proposal_row = await session.get(CommandRow, consumed_proposal_id, with_for_update=True)
                if (
                    proposal_row is None
                    or proposal_row.story_id != story.id
                    or proposal_row.output.rewrite is None
                    or proposal_row.output.rewrite.status != "proposed"
                    or proposal_row.output.rewrite.source_revision_id != submission.base_revision_id
                ):
                    raise ConflictError("建议已被接受、拒绝或不属于当前版本")
                updated_output = proposal_row.output.model_copy(deep=True)
                updated_output.rewrite.status = "accepted"
                proposal_row.output = updated_output
            from ainovel.mapper.field_history import append

            await append(session, revision)
            session.add(RevisionRow(id=revision.id, story_id=story.id, payload=revision))
            if confirmed_generation_id is not None:
                generation = await session.get(CommandRow, confirmed_generation_id, with_for_update=True)
                if generation is None or generation.status != "waiting" or generation.undone_at is not None:
                    raise ConflictError("草稿已停止或已处理")
                if (
                    generation.story_id != story.id
                    or generation.payload.base_revision_id != submission.base_revision_id
                ):
                    raise ConflictError("草稿不属于当前版本")
                if (
                    generation.output.writing_draft is None
                    or writing_draft is None
                    or generation.output.writing_draft.version != writing_draft.version
                ):
                    raise ConflictError("草稿版本已变化")
                generation.status = "completed"
                generation.output = output
                session.add(
                    EventRow(
                        command_id=generation.id,
                        payload=Event(command_id=generation.id, type="draft.confirmed", output=output),
                    )
                )
            session.add_all(
                [
                    ChunkRow(
                        chunk_id=chunk.id,
                        revision_id=revision.id,
                        chapter_id=chunk.chapter_id,
                        kind=chunk.kind,
                        payload=chunk,
                        embedding=embedding,
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ]
            )
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None:
                raise ValueError("命令不存在")
            if command.status != "running":
                raise ConflictError("任务已经停止，不能提交小说")
            command.status = "completed"
            command.output = output
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="story.committed", output=output),
                )
            )
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=output),
                )
            )
        return output

    async def set_deleted(self, submission: CommandSubmission, deleted: bool) -> CommandOutput:
        if submission.story_id is None:
            raise ValueError("缺少小说 ID")
        output = CommandOutput(
            story_id=submission.story_id,
            story_deleted=deleted,
            story_restored=not deleted,
            message="小说已移入回收站" if deleted else "小说已恢复",
        )
        async with self.sessions.begin() as session:
            await self.lock_operation_scope(session, submission.story_id)
            story = await session.get(StoryRow, submission.story_id, with_for_update=True)
            if story is None:
                raise ValueError("小说不存在")
            if story.head_id != submission.base_revision_id:
                raise ConflictError("小说版本已变化，请刷新后重试")
            pending_ids = (
                list(
                    (
                        await session.scalars(
                            select(CommandRow.id).where(
                                CommandRow.story_id == story.id,
                                CommandRow.id != submission.id,
                                CommandRow.status.in_(("queued", "running", "waiting")),
                            )
                        )
                    ).all()
                )
                if deleted
                else []
            )
            await self.record_operation(session, submission, related_ids=pending_ids)
            story.deleted_at = now() if deleted else None
            if deleted:
                pending = list(
                    (
                        await session.scalars(
                            select(CommandRow)
                            .where(
                                CommandRow.story_id == story.id,
                                CommandRow.id != submission.id,
                                CommandRow.status.in_(("queued", "running", "waiting")),
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for command in pending:
                    command.status = "failed"
                    command.error = "小说已删除，任务已取消"
                    session.add(
                        EventRow(
                            command_id=command.id,
                            payload=Event(
                                command_id=command.id,
                                type="run.failed",
                                output=CommandOutput(message=command.error),
                            ),
                        )
                    )
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running":
                raise ConflictError("删除或恢复任务已经停止")
            command.status = "completed"
            command.output = output
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(
                        command_id=submission.id,
                        type="story.deleted" if deleted else "story.restored",
                        output=output,
                    ),
                )
            )
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=output),
                )
            )
        return output

    async def chat_history(self, request: CommandSubmission) -> list[CommandView]:
        from ainovel.core.domain import CharacterChatRequest

        payload = request.payload
        if not isinstance(payload, CharacterChatRequest):
            return []
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    self.command_projection_query()
                    .where(
                        CommandRow.story_id == request.story_id,
                        CommandRow.status == "completed",
                        CommandRow.undone_at.is_(None),
                    )
                    .order_by(CommandRow.created_at)
                )
            ).all()
            matches = [
                self.projected_view(self.command_projection(row))
                for row in rows
                if isinstance(row.submission.payload, CharacterChatRequest)
                and row.submission.payload.session_id == payload.session_id
            ]
            for match in matches:
                previous = match.submission.payload
                if isinstance(previous, CharacterChatRequest) and (
                    previous.character_id != payload.character_id
                    or previous.scene_id != payload.scene_id
                    or match.submission.base_revision_id != request.base_revision_id
                ):
                    raise ValueError("角色会话绑定固定的版本、场景和角色，请新建会话")
            return matches

    async def commands(self, story_id: UUID) -> list[CommandView]:
        async with self.sessions() as session:
            await self.active_story(session, story_id)
            rows = (
                await session.execute(
                    self.command_projection_query()
                    .where(CommandRow.story_id == story_id)
                    .order_by(CommandRow.created_at)
                )
            ).all()
            return [self.projected_view(self.command_projection(row)) for row in rows]

    async def session_commands(self, session_id: UUID) -> list[CommandView]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    self.command_projection_query()
                    .where(CommandRow.story_id.is_(None))
                    .order_by(CommandRow.created_at)
                )
            ).all()
            return [
                self.projected_view(self.command_projection(row))
                for row in rows
                if row.submission.session_id == session_id
            ]

    async def record_operation(
        self,
        session: AsyncSession,
        submission: CommandSubmission,
        story_id: UUID | None = None,
        related_ids: list[UUID] | None = None,
    ) -> None:
        """Capture the inverse in the SAME transaction as the command's effects.

        New command handlers call this before changing durable state. Immutable revisions,
        chunks and audit events are retained; only their active pointers are compensated.
        """
        scope_id = story_id or submission.operation_scope_id
        await self.lock_operation_scope(session, scope_id)
        await self.check_resource(session, scope_id, allow_deleted=True)
        if submission.session_id is not None:
            await self.lock_operation_scope(session, submission.session_id)
            await self.check_resource(session, submission.session_id, allow_deleted=submission.payload.kind in (
                "set_setup_draft_deleted", "undo", "recover_command",
            ))
        if isinstance(submission.payload, SavePromptTemplateRequest):
            already_recorded = await session.scalar(
                select(OperationRow.id).where(OperationRow.command_id == submission.id).limit(1)
            )
            if already_recorded is not None:
                return
        readonly_chat = submission.payload.kind in ("discussion", "start_discussion", "character_chat", "start_character_chat")
        if submission.payload.kind in ("discussion", "start_discussion") and submission.payload.mode == "planning":
            readonly_chat = False
        before = OperationMemento(story_id=None if readonly_chat else story_id or submission.story_id)
        if submission.payload.kind == "set_setup_draft_deleted":
            before.draft_id = submission.session_id
            barrier = await session.get(ResourceDeletionRow, submission.session_id)
            before.draft_deleted = bool(barrier and barrier.status == "deleted")
        if before.story_id:
            story = await session.get(StoryRow, before.story_id, with_for_update=True)
            if story:
                before.story_existed = True
                before.head_id = story.head_id
                before.deleted_at = story.deleted_at
                before.title = story.title
        if isinstance(submission.payload, SavePromptTemplateRequest):
            binding = await session.scalar(
                select(PromptTemplateBindingRow).where(
                    PromptTemplateBindingRow.scope_id == scope_id,
                    PromptTemplateBindingRow.task == submission.payload.task,
                )
            )
            before.prompt_template_bindings.append(
                PromptTemplateBindingMemento(
                    scope_id=scope_id,
                    task=submission.payload.task,
                    version_id=binding.version_id if binding else None,
                )
            )
        for command_id in related_ids or []:
            row = await session.scalar(
                select(CommandRow)
                .options(
                    load_only(
                        CommandRow.id,
                        CommandRow.status,
                        CommandRow.output,
                        CommandRow.error,
                        CommandRow.undone_at,
                    )
                )
                .where(CommandRow.id == command_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("关联命令不存在")
            raw_checkpoint = await session.scalar(
                select(cast(CommandRow.__table__.c.checkpoint, Text)).where(CommandRow.id == command_id)
            )
            checkpoint = None
            checkpoint_raw = None
            if raw_checkpoint is not None:
                try:
                    checkpoint = WritingState.model_validate_json(raw_checkpoint)
                except ValidationError:
                    checkpoint_raw = raw_checkpoint
            before.commands.append(
                CommandMemento(
                    command_id=row.id,
                    status=row.status,
                    output=row.output,
                    checkpoint=checkpoint,
                    checkpoint_raw=checkpoint_raw,
                    error=row.error,
                    undone_at=row.undone_at,
                )
            )
        session.add(OperationRow(scope_id=scope_id, command_id=submission.id, before=before))

    @staticmethod
    async def check_resource(session: AsyncSession, resource_id: UUID, allow_deleted: bool = False) -> None:
        barrier = await session.get(ResourceDeletionRow, resource_id)
        if barrier and (barrier.status == "purged" or not allow_deleted):
            raise ConflictError("资料已永久删除" if barrier.status == "purged" else "创作草稿已移入回收站，请先恢复")

    async def lock_operation_scope(self, session: AsyncSession, scope_id: UUID) -> None:
        if self.engine.dialect.name == "postgresql":
            # Negative keys keep journal locks separate from positive worker command leases.
            await session.execute(select(func.pg_advisory_xact_lock(-1 - scope_id.int % (2**63 - 1))))

    async def claim_lock(self, command_id: UUID) -> AsyncIterator[AsyncSession]:
        """Used via contextlib.aclosing by workers; lock survives short application transactions."""
        async with self.sessions() as session:
            key = command_id.int % (2**63 - 1)
            if self.engine.dialect.name == "postgresql":
                acquired = await session.scalar(select(func.pg_try_advisory_lock(key)))
                if not acquired:
                    return
            try:
                yield session
            finally:
                if self.engine.dialect.name == "postgresql":
                    await session.execute(select(func.pg_advisory_unlock(key)))
