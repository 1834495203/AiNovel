"""Sequential command compensation. The durable journal is the authoritative undo stack."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from ainovel.core.domain import CommandOutput, CommandSubmission, Event, UndoRequest, UndoState
from ainovel.core.errors import ConflictError
from ainovel.mapper.models import (
    CommandRow,
    EventRow,
    OperationRow,
    PromptTemplateBindingRow,
    SetupDraftRow,
    StoryRow,
)
from ainovel.mapper.store import Store
from ainovel.utils.time import now


class UndoService:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    async def latest(session: AsyncSession, scope_id: UUID) -> OperationRow | None:
        return await session.scalar(
            select(OperationRow)
            .where(OperationRow.scope_id == scope_id, OperationRow.undone_by.is_(None))
            .order_by(OperationRow.id.desc())
            .limit(1)
        )

    async def state(self, scope_id: UUID) -> UndoState:
        async with self.store.sessions() as session:
            operation = await self.latest(session, scope_id)
            if operation is None:
                return UndoState(message="没有可撤回的操作；升级前未保存撤回凭据的记录仍可通过版本历史恢复。")
            target = await session.get(CommandRow, operation.command_id)
            if target is None:
                raise ValueError("操作日志损坏")
            return UndoState(
                operation_id=operation.id,
                command_id=target.id,
                kind=target.payload.payload.kind,
                available=True,
                command_ids=list(
                    (
                        await session.scalars(
                            select(OperationRow.command_id).where(
                                OperationRow.scope_id == scope_id, OperationRow.undone_by.is_(None)
                            )
                        )
                    ).all()
                ),
            )

    async def execute(self, submission: CommandSubmission, request: UndoRequest) -> CommandOutput:
        scope_id = submission.story_id or submission.session_id
        if scope_id is None:
            raise ValueError("撤回需要小说或创建会话")
        async with self.store.sessions.begin() as session:
            await self.store.lock_operation_scope(session, scope_id)
            operation = await self.latest(session, scope_id)
            if operation is None or operation.id != request.expected_operation_id:
                raise ConflictError("最新操作已变化，请刷新后再次撤回")
            operations = [operation]
            if request.target_command_id:
                if request.target_command_id != operation.command_id:
                    raise ConflictError("只能撤回最新操作，请按顺序逐次撤回")
            allowed_pending = [
                item.command_id for operation in operations for item in operation.before.commands
            ]
            pending_query = select(CommandRow).where(
                CommandRow.status.in_(("queued", "running")),
                CommandRow.undone_at.is_(None),
                CommandRow.id != submission.id,
            )
            # Scope the SQL query before ORM materialization. A different story may
            # have an old or malformed checkpoint; loading every queued command
            # would deserialize that unrelated state before the Python filter.
            pending_query = pending_query.where(
                CommandRow.story_id == scope_id
                if submission.story_id is not None
                else CommandRow.story_id.is_(None)
            )
            pending = (await session.scalars(pending_query)).all()
            for row in pending:
                if row.payload.payload.kind in ("undo", "save_setup_draft"):
                    continue
                same_scope = row.story_id == scope_id or row.payload.session_id == scope_id
                if not same_scope:
                    continue
                await session.refresh(row, with_for_update=True)
                if row.undone_at is not None or row.status not in ("queued", "running"):
                    continue
                if row.id not in allowed_pending or row.status == "running":
                    raise ConflictError("当前操作尚未完成，请完成后撤回")
            story = (
                await session.get(StoryRow, submission.story_id, with_for_update=True)
                if submission.story_id
                else None
            )
            if story and story.head_id != submission.base_revision_id:
                raise ConflictError("小说版本已变化，请刷新后再次撤回")
            undone_ids: list[UUID] = []
            for operation in operations:
                await self.compensate(session, submission, operation, story)
                undone_ids.append(operation.command_id)
            output = CommandOutput(
                story_id=submission.story_id,
                revision_id=story.head_id if story else None,
                story_deleted=bool(story and story.deleted_at),
                undone_command_id=undone_ids[-1],
                undone_command_ids=undone_ids,
                message="已撤回最近一次完整操作"
                if len(operations) == 1
                else f"已连续撤回 {len(operations)} 次操作",
            )
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running":
                raise ConflictError("撤回命令已停止")
            if submission.story_id is None:
                draft_row = await session.get(SetupDraftRow, scope_id, with_for_update=True)
                if draft_row:
                    rows = (
                        await session.scalars(
                            select(CommandRow)
                            .where(
                                CommandRow.story_id.is_(None),
                                CommandRow.undone_at.is_(None),
                                CommandRow.status == "completed",
                            )
                            .order_by(CommandRow.created_at)
                        )
                    ).all()
                    interviews = [
                        row for row in rows if row.payload.session_id == scope_id and row.output.interview
                    ]
                    draft = draft_row.payload.model_copy(deep=True)
                    draft.proposal = interviews[-1].output.interview.proposal if interviews else None
                    draft.edits = []
                    draft.editing_setup = False
                    draft.title = draft.proposal.title if draft.proposal else "新故事草稿"
                    # Pre-undo autosaves delivered late cannot bring removed setup edits back.
                    draft.updated_at = command.created_at
                    accepted = await session.scalar(
                        select(EventRow)
                        .where(EventRow.command_id == submission.id)
                        .order_by(EventRow.id)
                        .limit(1)
                    )
                    if accepted is None:
                        raise ConflictError("撤回请求缺少接收记录")
                    draft.save_event_id = accepted.id
                    draft_row.payload = draft
            command.status = "completed"
            command.output = output
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=output),
                )
            )
            return output

    async def compensate(
        self,
        session: AsyncSession,
        submission: CommandSubmission,
        operation: OperationRow,
        story: StoryRow | None,
    ) -> None:
        before = operation.before
        if before.story_existed:
            if story is None or before.head_id is None:
                raise ValueError("撤回存档缺失")
            story.head_id = before.head_id
            story.deleted_at = before.deleted_at
            if before.title is not None:
                story.title = before.title
        elif story:
            # Undo creation by hiding the story; immutable artifacts remain available to audit.
            story.deleted_at = now()
        for saved in before.commands:
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
                .where(CommandRow.id == saved.command_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("关联命令缺失")
            row.status = "queued" if saved.status == "running" else saved.status
            row.output = saved.output
            if saved.checkpoint_raw is not None:
                await session.execute(
                    text("UPDATE commands SET checkpoint = :checkpoint WHERE id = :command_id"),
                    {"checkpoint": saved.checkpoint_raw, "command_id": row.id},
                )
            else:
                row.checkpoint = saved.checkpoint
            row.error = saved.error
            row.undone_at = saved.undone_at
            session.add(
                EventRow(
                    command_id=row.id,
                    payload=Event(command_id=row.id, type="command.restored", output=saved.output),
                )
            )
        for saved in before.prompt_template_bindings:
            binding = await session.scalar(
                select(PromptTemplateBindingRow)
                .where(
                    PromptTemplateBindingRow.scope_id == saved.scope_id,
                    PromptTemplateBindingRow.task == saved.task,
                )
                .with_for_update()
            )
            if saved.version_id is None:
                if binding is not None:
                    await session.delete(binding)
            elif binding is None:
                session.add(
                    PromptTemplateBindingRow(
                        scope_id=saved.scope_id,
                        task=saved.task,
                        version_id=saved.version_id,
                    )
                )
            else:
                binding.version_id = saved.version_id
        target = await session.get(CommandRow, operation.command_id, with_for_update=True)
        if target is None:
            raise ValueError("原始命令缺失")
        target.undone_at = now()
        if target.status in ("queued", "running", "waiting"):
            target.status = "failed"
            target.error = "用户已撤回此操作"
        operation.undone_by = submission.id
        session.add(
            EventRow(
                command_id=target.id,
                payload=Event(
                    command_id=target.id,
                    type="command.undone",
                    output=CommandOutput(undone_command_id=target.id),
                ),
            )
        )
