"""Draft feedback is a command; canonical changes require explicit confirmation."""

from contextlib import aclosing

from ainovel.core.domain import (
    CommandOutput,
    CommandSubmission,
    DraftReviewResponse,
    Event,
    ReviewDraftRequest,
    WorkflowState,
    WritingDraft,
)
from ainovel.core.errors import ConflictError
from ainovel.mapper.models import CommandRow, EventRow
from ainovel.service.commands import Handler


class ReviewDraftHandler(Handler[ReviewDraftRequest]):
    request_type = ReviewDraftRequest

    async def execute(self, submission: CommandSubmission, request: ReviewDraftRequest) -> CommandOutput:
        store = self.services.store
        async with aclosing(store.claim_lock(request.generation_command_id)) as locks:
            async for _lease in locks:
                parent = await store.command(request.generation_command_id)
                pending = parent.output.writing_draft
                if (
                    parent.status != "waiting"
                    or parent.undone
                    or parent.submission.story_id != submission.story_id
                    or pending is None
                    or pending.status != "pending"
                ):
                    raise ConflictError("这份草稿未等待确认或不属于当前小说")
                if parent.submission.base_revision_id != submission.base_revision_id:
                    raise ConflictError("草稿源版本不一致")
                if pending.version != request.expected_draft_version:
                    raise ConflictError("草稿已有新版本，请刷新后重试")
                current = await store.head(submission.story_id)
                if request.decision != "discard" and current.revision_id != submission.base_revision_id:
                    raise ConflictError("正式故事已变化，请重新创作；旧草稿仍保留")
                if request.selection and (
                    request.selection.end > len(pending.content.text)
                    or pending.content.text[request.selection.start : request.selection.end]
                    != request.selection.selected_text
                ):
                    raise ConflictError("选区与当前草稿不一致")
                state = await store.checkpoint(parent.submission.id)
                if state is None or state.draft_status != "pending":
                    raise ConflictError("草稿检查点缺失")
                # The parent remains at its waiting gate throughout an attempt.
                # Failed calls and undo resume that precise, immutable checkpoint.
                attempt = await store.checkpoint(submission.id)
                if attempt is not None:
                    state = attempt
                else:
                    state = state.model_copy(deep=True)
                    state.execution_command_id = submission.id
                    state.text_only_revision = state.text_only_revision or request.text_only
                    state.review_response = DraftReviewResponse(
                        text_only=state.text_only_revision,
                        decision=request.decision,
                        execution_command_id=submission.id,
                        feedback=request.feedback,
                        selection=request.selection,
                    )
                    if request.decision == "confirm":
                        await self.reuse_confirmation(submission, request, state)
                state = await self.services.workflow.run(state)
                if state.approval_phase:
                    output = CommandOutput(
                        need=state.plan.new_character if state.plan else None,
                        approval_phase=state.approval_phase,
                        message="修改草稿需要新角色，请先确认引入",
                    )
                    await store.finish(submission.id, output, "waiting")
                    return output
                if state.draft is None:
                    raise ValueError("缺少草稿正文")
                draft = WritingDraft(
                    generation_command_id=parent.submission.id,
                    version=state.draft_version,
                    content=state.draft,
                    status="confirmed"
                    if state.draft_status == "confirmed"
                    else "discarded"
                    if state.draft_status == "discarded"
                    else "pending",
                )
                if request.decision == "confirm":
                    if state.draft_status != "confirmed":
                        raise ValueError("草稿未被确认")
                    return await self.services.commit(
                        submission,
                        self.services.apply_scene(state),
                        "确认草稿，提交正文与相关设定",
                        state.extraction,
                        confirmed_generation_id=parent.submission.id,
                        writing_draft=draft,
                    )
                output = CommandOutput(
                    story_id=submission.story_id,
                    writing_draft=draft,
                    message="草稿已修改，请再次确认"
                    if draft.status == "pending"
                    else "已放弃草稿，正式故事保持原状态",
                )
                await self.complete_review(submission, state, output)
                return output
        raise ConflictError("草稿正在处理，请稍后重试")

    async def reuse_confirmation(
        self, submission: CommandSubmission, request: ReviewDraftRequest, state: WorkflowState
    ) -> None:
        """A second client command may retry an identical draft after an earlier failure.

        Reuse only validated outputs from active failed confirmations of this exact
        version, base story and prose. Undone operations and revised drafts are excluded.
        """
        store = self.services.store
        for previous in reversed(await store.commands(submission.story_id)):
            payload = previous.submission.payload
            if (
                previous.status != "failed"
                or previous.undone
                or not isinstance(payload, ReviewDraftRequest)
                or payload.decision != "confirm"
                or payload.generation_command_id != request.generation_command_id
                or payload.expected_draft_version != request.expected_draft_version
                or previous.submission.base_revision_id != submission.base_revision_id
            ):
                continue
            cached = await store.checkpoint(previous.submission.id)
            if (
                cached is None
                or cached.draft_status != "confirmed"
                or cached.extraction is None
                or cached.scene_id != state.scene_id
                or cached.draft != state.draft
                or cached.story != state.story
            ):
                continue
            state.extraction = cached.extraction.model_copy(deep=True)
            state.maintenance = cached.maintenance.model_copy(deep=True) if cached.maintenance else None
            await self.services.workflow.step(
                state, "reuse_confirmation", "复用同一草稿已完成的确认步骤，继续失败的后续操作"
            )
            return

    async def complete_review(
        self, submission: CommandSubmission, state: WorkflowState, output: CommandOutput
    ) -> None:
        store = self.services.store
        parent_id = output.writing_draft.generation_command_id
        async with store.sessions.begin() as session:
            await store.record_operation(session, submission, related_ids=[parent_id])
            story = await store.active_story(session, submission.story_id, locked=True)
            if output.writing_draft.status != "discarded" and story.head_id != submission.base_revision_id:
                raise ConflictError("正式故事已变化，草稿结果未覆盖新版本")
            parent = await session.get(CommandRow, parent_id, with_for_update=True)
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if (
                parent is None
                or parent.status != "waiting"
                or parent.undone_at is not None
                or command is None
                or command.status != "running"
            ):
                raise ConflictError("草稿操作已停止")
            state.execution_command_id = None
            state.review_response = None
            parent.checkpoint = state
            parent.output = output
            parent.status = "waiting" if output.writing_draft.status == "pending" else "completed"
            command.status = "completed"
            command.output = output
            for identifier, event_type in ((submission.id, "run.completed"), (parent_id, "draft.updated")):
                session.add(
                    EventRow(
                        command_id=identifier,
                        payload=Event(command_id=identifier, type=event_type, output=output),
                    )
                )
