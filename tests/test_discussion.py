"""Discussion reads the live head, isolates sessions and never edits prose."""

import os
from uuid import UUID, uuid4

import pytest
from test_integration import Harness

from ainovel.core.discussion import (
    CharacterChange,
    DiscussionAction,
    DiscussionContext,
    DiscussionRequest,
    ReplyAction,
    StartDiscussionRequest,
    UpdateSettingsAction,
    UpdateSettingsArguments,
)
from ainovel.core.domain import (
    CommandSubmission,
    PromptTemplateVersion,
    RecoverCommandRequest,
    Trace,
    UndoRequest,
    UpdateStoryRequest,
)
from ainovel.core.records import (
    StoryMetadataPatch,
    WorldPatch,
)
from ainovel.llm.mock import MockModel
from ainovel.mapper.discussions import DiscussionRepository
from ainovel.service.undo import UndoService

pytestmark = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")


class ScriptedDiscussion(MockModel):
    def __init__(self, sink, actions: list[DiscussionAction | Exception]) -> None:
        super().__init__(sink)
        self.actions = actions
        self.contexts: list[DiscussionContext] = []

    async def choose_discussion_tool(
        self,
        context: DiscussionContext,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> DiscussionAction:
        self.contexts.append(context.model_copy(deep=True))
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        await self.sink.record(
            command_id,
            Trace(
                node="discussion",
                summary="讨论与工具选择",
                model="mock",
                structured_input=context.model_dump_json(),
                structured_output=action.model_dump_json(),
            ),
        )
        return action


async def session(h: Harness) -> UUID:
    id = uuid4()
    created = await h.execute(StartDiscussionRequest(session_id=id))
    assert created.status == "completed", created.error
    return id


async def test_discussion_old_session_and_stale_submission_read_latest_without_graph() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        old = h.head
        id = await session(h)
        changed = await h.execute(
            UpdateStoryRequest(patch=StoryMetadataPatch(world=WorldPatch(premise="最新世界规则")))
        )
        submission = CommandSubmission(
            story_id=h.story_id,
            base_revision_id=old,
            payload=DiscussionRequest(session_id=id, message="聊聊后续故事"),
        )
        h.ids.append(submission.id)
        await h.runtime.store.submit(submission)
        await h.worker.process(submission.id)
        reply = await h.runtime.store.command(submission.id)
        assert reply.status == "completed", reply.error
        assert "最新世界规则" in reply.output.discussion.reply
        assert reply.output.discussion.read_revision_id == changed.output.revision_id
        assert reply.output.revision_id is None and not reply.output.discussion.settings_changed
        assert not h.runtime.services.workflow.checkpoints.ready
        history = await DiscussionRepository(h.runtime.store).history(h.story_id, id)
        assert len(history) == 2
        state = await UndoService(h.runtime.store).state(id)
        await h.execute(UndoRequest(scope_id=id, expected_operation_id=state.operation_id))
        assert await DiscussionRepository(h.runtime.store).history(h.story_id, id) == []
    finally:
        await h.cleanup()


async def test_discussion_rejects_mutating_model_actions_without_committing():
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        id = await session(h)
        before = await h.runtime.store.head(h.story_id)
        h.runtime.services.model = ScriptedDiscussion(h.runtime.sink, [UpdateSettingsAction(
            arguments=UpdateSettingsArguments(patch=StoryMetadataPatch(world=WorldPatch(premise="禁止写入")), reason="修改要求")
        )])
        result = await h.execute(DiscussionRequest(session_id=id, message="修改世界观"))
        assert result.status == "failed" and "只能查询和讨论" in result.error
        assert await h.runtime.store.head(h.story_id) == before
        assert await DiscussionRepository(h.runtime.store).history(h.story_id, id) == []
    finally:
        await h.cleanup()


async def test_discussion_sessions_isolate_history_but_share_latest_settings() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        first, second = await session(h), await session(h)
        reply = await h.execute(DiscussionRequest(session_id=first, message="修改世界观：共同的新世界"))
        assert reply.status == "completed", reply.error
        script = ScriptedDiscussion(
            h.runtime.sink, [ReplyAction(reply="第二会话"), ReplyAction(reply="第一会话继续")]
        )
        h.runtime.services.model = script
        await h.execute(DiscussionRequest(session_id=second, message="你读取了什么？"))
        assert script.contexts[0].history == []
        assert script.contexts[0].story.world.premise == "近未来都市"
        await h.execute(DiscussionRequest(session_id=first, message="继续讨论"))
        assert len(script.contexts[1].history) == 2
        sessions = await DiscussionRepository(h.runtime.store).sessions(h.story_id)
        assert sorted(item.message_count for item in sessions) == [1, 2]
    finally:
        await h.cleanup()


async def test_discussion_failure_has_no_partial_settings_and_retry_reads_new_head() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        id = await session(h)
        before = await h.runtime.store.head(h.story_id)
        h.runtime.services.model = ScriptedDiscussion(
            h.runtime.sink,
            [
                UpdateSettingsAction(
                    arguments=UpdateSettingsArguments(
                        reason="暂存修改", patch=StoryMetadataPatch(world=WorldPatch(premise="不应保存"))
                    )
                ),
                RuntimeError("模拟模型断线"),
            ],
        )
        failed = await h.execute(DiscussionRequest(session_id=id, message="修改世界观"))
        assert failed.status == "failed"
        assert await h.runtime.store.head(h.story_id) == before
        changed = await h.execute(
            UpdateStoryRequest(patch=StoryMetadataPatch(world=WorldPatch(premise="外部最新设定")))
        )
        script = ScriptedDiscussion(h.runtime.sink, [ReplyAction(reply="使用最新设定重试完成")])
        h.runtime.services.model = script
        events = await h.runtime.store.events(failed.submission.id)
        recovery = await h.execute(
            RecoverCommandRequest(target_command_id=failed.submission.id, expected_event_id=events[-1].id)
        )
        assert recovery.status == "completed", recovery.error
        await h.worker.process(failed.submission.id)
        done = await h.runtime.store.command(failed.submission.id)
        assert done.status == "completed", done.error
        assert done.output.discussion.read_revision_id == changed.output.revision_id
        assert script.contexts[0].story.world.premise == "外部最新设定"
        assert (await h.runtime.store.head(h.story_id)).story.world.premise == "外部最新设定"
    finally:
        await h.cleanup()


async def test_unknown_character_tool_cannot_write_from_discussion():
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        id = await session(h)
        before = await h.runtime.store.head(h.story_id)
        h.runtime.services.model = ScriptedDiscussion(h.runtime.sink, [UpdateSettingsAction(
            arguments=UpdateSettingsArguments(characters=[CharacterChange(character_id=uuid4(), reason="不存在")], reason="测试")
        )])
        result = await h.execute(DiscussionRequest(session_id=id, message="修改角色"))
        assert result.status == "failed"
        assert await h.runtime.store.head(h.story_id) == before
    finally:
        await h.cleanup()
