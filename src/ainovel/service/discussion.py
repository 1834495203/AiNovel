"""Ordinary discussion agent: optional tools and one atomic reversible turn."""

from contextlib import aclosing

from ainovel.core.discussion import (
    DiscussionContext,
    DiscussionExcerpt,
    DiscussionRequest,
    DiscussionResult,
    DiscussionSession,
    DiscussionToolResult,
    ReplyAction,
    SearchStoryAction,
    StartDiscussionRequest,
    UpdateSettingsAction,
)
from ainovel.core.domain import CommandOutput, CommandSubmission, Event, Trace
from ainovel.core.errors import ConflictError
from ainovel.mapper.discussions import DiscussionRepository
from ainovel.service.commands import Handler
from ainovel.service.discussion_tools import apply_settings, context_story
from ainovel.service.prompt_templates import resolve_command_template


class StartDiscussionHandler(Handler[StartDiscussionRequest]):
    request_type = StartDiscussionRequest

    async def execute(self, submission: CommandSubmission, request: StartDiscussionRequest) -> CommandOutput:
        await self.services.store.head(submission.story_id)
        if any(
            item.id == request.session_id
            for item in await DiscussionRepository(self.services.store).sessions(submission.story_id)
        ):
            raise ConflictError("讨论会话已存在")
        return CommandOutput(
            story_id=submission.story_id,
            discussion_session=DiscussionSession(
                id=request.session_id, story_id=submission.story_id, title=request.title
            ),
            message="已创建故事讨论会话",
        )


class DiscussionHandler(Handler[DiscussionRequest]):
    request_type = DiscussionRequest

    async def execute(self, submission: CommandSubmission, request: DiscussionRequest) -> CommandOutput:
        async with aclosing(self.services.store.claim_lock(request.session_id)) as locks:
            async for _lease in locks:
                return await self.turn(submission, request)
        raise ConflictError("此会话正在回复，请等待本轮完成")

    async def turn(self, submission: CommandSubmission, request: DiscussionRequest) -> CommandOutput:
        store = self.services.store
        history = await DiscussionRepository(store).history(submission.story_id, request.session_id)
        latest = await store.head(submission.story_id)
        prompt_template = await resolve_command_template(
            store, submission.id, submission.story_id, "discussion"
        )
        working = latest.story.model_copy(deep=True)
        results: list[DiscussionToolResult] = []
        for _ in range(self.services.settings.discussion_max_steps):
            if (await store.head(submission.story_id)).revision_id != latest.revision_id:
                raise ConflictError("讨论期间小说已更新，本轮未保存修改。请重试以读取最新状态。")
            context = DiscussionContext(
                story=context_story(working, latest.revision_id, self.services.settings.recent_scenes),
                history=history,
                message=request.message,
                tool_results=results,
            )
            action = await self.services.model.choose_discussion_tool(
                context, submission.id, prompt_template=prompt_template
            )
            if isinstance(action, ReplyAction):
                if (await store.head(submission.story_id)).revision_id != latest.revision_id:
                    raise ConflictError("小说已更新，本轮未保存修改。请重试以读取最新状态。")
                changed = working != latest.story
                result = DiscussionResult(
                    session_id=request.session_id,
                    reply=action.reply,
                    read_revision_id=latest.revision_id,
                    settings_changed=changed,
                    tool_results=results,
                )
                if changed:
                    effective = submission.model_copy(update={"base_revision_id": latest.revision_id})
                    return await self.services.commit(
                        effective, working, "故事讨论：更新设定与角色，正文保持原样", discussion=result
                    )
                return CommandOutput(story_id=submission.story_id, discussion=result, message="讨论已保存")
            summary = (
                "按需召回小说内容"
                if isinstance(action, SearchStoryAction)
                else "暂存设定修改，本轮完成后统一保存"
            )
            await store.append_event(
                Event(
                    command_id=submission.id,
                    actor="ai",
                    type="step.started",
                    trace=Trace(
                        node=action.kind, summary=summary, structured_input=action.arguments.model_dump_json()
                    ),
                )
            )
            if isinstance(action, SearchStoryAction):
                chunks = await self.services.retrieval.narrative(
                    latest.revision_id, action.arguments.query, submission.id
                )
                outcome = DiscussionToolResult(
                    name="search_story",
                    message=f"召回 {len(chunks)} 条内容，已去重并按故事时间排序",
                    excerpts=[DiscussionExcerpt(title=chunk.kind, text=chunk.content) for chunk in chunks],
                )
            elif isinstance(action, UpdateSettingsAction):
                try:
                    candidate = apply_settings(working, action.arguments)
                    changed = candidate != working
                    working = candidate
                    outcome = DiscussionToolResult(
                        name="update_story_settings",
                        message=action.arguments.reason
                        + ("；已暂存，本轮完成后保存" if changed else "；未产生新变更"),
                    )
                except ValueError as error:
                    outcome = DiscussionToolResult(
                        name="update_story_settings", success=False, message=str(error)
                    )
            else:
                raise ValueError("未知讨论工具")
            results.append(outcome)
            await store.append_event(
                Event(
                    command_id=submission.id,
                    actor="ai",
                    type="step.completed",
                    trace=Trace(
                        node=action.kind, summary="工具已返回", structured_output=outcome.model_dump_json()
                    ),
                )
            )
        raise ValueError("讨论达到工具调用上限，本轮未保存修改，请缩小讨论范围后重试")
