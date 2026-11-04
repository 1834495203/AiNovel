"""Ordinary discussion agent: optional tools and one atomic reversible turn."""

from contextlib import aclosing

from ainovel.core.discussion import (
    DiscussionRequest,
    DiscussionResult,
    DiscussionSession,
    DiscussionToolResult,
    SaveScenePlansAction,
    StartDiscussionRequest,
)
from ainovel.core.domain import CommandOutput, CommandSubmission
from ainovel.core.errors import ConflictError
from ainovel.mapper.discussions import DiscussionRepository
from ainovel.service.commands import Handler
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
                id=request.session_id, story_id=submission.story_id, title=request.title, mode=request.mode
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
            store,
            submission.id,
            submission.story_id,
            "scene_planning" if request.mode == "planning" else "discussion",
        )
        if request.mode == "planning":
            session = next(
                (
                    item
                    for item in await DiscussionRepository(store).sessions(submission.story_id)
                    if item.id == request.session_id
                ),
                None,
            )
            if session is None or session.mode != request.mode:
                raise ValueError("会话类型不匹配，请打开正确的设定会话")
        from ainovel.core.agent import SubmitCreationAction, SubmitDraftAction
        from ainovel.service.agent_loop import context_for, read_tool, run_agent

        working = latest.story.model_copy(deep=True)
        base = working.model_copy(deep=True)
        changed = False
        results = []
        task = "scene_planning" if request.mode == "planning" else "discussion"

        def build(items):
            return context_for(
                base,
                latest.revision_id,
                task,
                request.message,
                history=history,
                results=items,
                recent=self.services.settings.recent_scenes,
            )

        async def execute(action):
            nonlocal working, changed
            if isinstance(action, SaveScenePlansAction) and request.mode == "planning":
                from ainovel.service.scene_planning import apply_scene_batch

                try:
                    candidate = working.model_copy(deep=True)
                    apply_scene_batch(candidate, action.arguments)
                    working = candidate
                    changed = changed or bool(
                        action.arguments.create or action.arguments.update or action.arguments.select_leaf_id
                    )
                    return DiscussionToolResult(
                        name="save_scene_plans",
                        message="幕修改已暂存，本轮结束时提交。",
                        affected_scene_ids=[
                            n.id for n in [*action.arguments.create, *action.arguments.update]
                        ],
                    )
                except ValueError as error:
                    return DiscussionToolResult(name="save_scene_plans", success=False, message=str(error))
            return await read_tool(self.services, working, latest.revision_id, action, submission.id)

        action = await run_agent(
            self.services, submission.id, task, prompt_template, build, execute, results=results
        )
        if isinstance(action, (SubmitDraftAction, SubmitCreationAction)):
            raise ValueError("本会话不能提交正文或创建小说")
        result = DiscussionResult(
            session_id=request.session_id,
            reply=action.reply,
            read_revision_id=latest.revision_id,
            settings_changed=changed,
            tool_results=results,
        )
        if changed:
            if submission.base_revision_id != latest.revision_id:
                raise ConflictError("幕设定已被其他会话修改，请刷新后重新提交，本轮未保存")
            return await self.services.commit(submission, working, "保存幕设定", discussion=result)
        return CommandOutput(story_id=submission.story_id, discussion=result, message="会话已保存")
