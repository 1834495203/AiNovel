"""Explicit, journaled recovery of a failed command; DB outbox delivers retries."""

from sqlalchemy import select
from sqlalchemy.orm import load_only

from ainovel.core.domain import CommandOutput, CommandSubmission, Event, RecoverCommandRequest
from ainovel.core.errors import ConflictError
from ainovel.mapper.models import CommandRow, EventRow, StoryRow
from ainovel.service.commands import Handler
from ainovel.utils.time import now


class RecoverCommandHandler(Handler[RecoverCommandRequest]):
    request_type = RecoverCommandRequest

    async def execute(self, submission: CommandSubmission, request: RecoverCommandRequest) -> CommandOutput:
        store = self.services.store
        scope = submission.story_id or submission.session_id
        if scope is None:
            raise ValueError("恢复任务需要小说或创建会话")
        async with store.sessions.begin() as session:
            await store.lock_operation_scope(session, scope)
            target = await session.scalar(
                select(CommandRow)
                .options(
                    load_only(
                        CommandRow.id,
                        CommandRow.status,
                        CommandRow.payload,
                        CommandRow.output,
                        CommandRow.error,
                        CommandRow.created_at,
                        CommandRow.undone_at,
                    )
                )
                .where(CommandRow.id == request.target_command_id)
                .with_for_update()
            )
            if target is None or target.status != "failed" or target.undone_at is not None:
                raise ConflictError("任务已恢复或已撤回，请刷新后重试")
            source = target.payload
            if source.story_id != submission.story_id or (
                source.story_id is None and (source.session_id or source.id) != submission.session_id
            ):
                raise ConflictError("失败任务不属于当前小说或创建会话")
            if source.payload.kind in ("undo", "recover_command", "save_setup_draft"):
                raise ConflictError("请重新执行当前撤回或保存操作")
            if request.decision == "retry" and (target.error or "").startswith("CheckpointValidationError:"):
                raise ConflictError("此任务的检查点无法读取；原始数据已保留，不能安全重试，请撤回失败任务")
            failure = await session.scalar(
                select(EventRow).where(EventRow.command_id == target.id).order_by(EventRow.id.desc()).limit(1)
            )
            if failure is None or failure.id != request.expected_event_id:
                raise ConflictError("任务失败状态已变化，请刷新后重试")
            if request.decision == "retry" and source.story_id is not None:
                story = await session.get(StoryRow, source.story_id, with_for_update=True)
                if story is None or story.deleted_at is not None:
                    raise ConflictError("小说已删除，请先恢复小说")
                if (
                    source.payload.kind
                    not in ("character_chat", "start_character_chat", "discussion", "start_discussion")
                    and story.head_id != source.base_revision_id
                ):
                    raise ConflictError("小说版本已变化，请重新发起创作；旧失败任务可以撤回")
            await store.record_operation(session, submission, related_ids=[target.id])
            if request.decision == "retry":
                target.status = "queued"
                target.error = None
            else:
                target.undone_at = now()
            output = CommandOutput(
                recovered_command_id=target.id,
                recovery_decision=request.decision,
                message="已安排重试，将从已有检查点继续" if request.decision == "retry" else "已撤回失败任务",
            )
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running" or command.undone_at is not None:
                raise ConflictError("恢复操作已停止")
            command.status = "completed"
            command.output = output
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=output),
                )
            )
            session.add(
                EventRow(
                    command_id=target.id,
                    payload=Event(
                        command_id=target.id,
                        type="command.retry_requested"
                        if request.decision == "retry"
                        else "command.withdrawn",
                        output=output,
                    ),
                )
            )
            return output
