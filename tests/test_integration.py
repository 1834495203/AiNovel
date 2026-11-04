"""Opt-in real PostgreSQL + pgvector + Redis tests. Only this fixture's records are deleted."""

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from configured_fields import make_world
from sqlalchemy import delete, select

from ainovel.bootstrap.runtime import Runtime
from ainovel.controller.api import create_app
from ainovel.core.characters import CharacterSpec
from ainovel.core.config import Settings
from ainovel.core.domain import (
    AcceptRewriteRequest,
    CharacterChatRequest,
    CommandSubmission,
    CommandView,
    CreateStoryRequest,
    CreationProposal,
    Direction,
    DirectionRequest,
    GenerateRequest,
    InterviewRequest,
    PromptTemplateSelection,
    ReindexRequest,
    RejectRewriteRequest,
    Request,
    RevertRequest,
    ReviewDraftRequest,
    RewriteRequest,
    SavePromptTemplateRequest,
    Turn,
    UndoRequest,
)
from ainovel.infrastructure.job_queue import JobQueue
from ainovel.mapper.models import (
    ChunkRow,
    CommandRow,
    EventRow,
    FieldChangeRow,
    FieldSnapshotRow,
    FieldTemplateRow,
    FieldTemplateSetRow,
    OperationRow,
    PromptTemplateBindingRow,
    PromptTemplateVersionRow,
    RevisionRow,
    SetupDraftRow,
    StoryRow,
)
from ainovel.service.undo import UndoService
from ainovel.service.worker import Worker

pytestmark = pytest.mark.skipif(
    os.getenv("AINOVEL_INTEGRATION") != "1", reason="Set AINOVEL_INTEGRATION=1 for local PostgreSQL/Redis"
)


class Harness:
    def __init__(self, settings: Settings | None = None, auto_confirm: bool = True) -> None:
        self.runtime = Runtime(settings or Settings(llm_provider="mock"))
        self.queue = JobQueue(self.runtime.settings.redis_url, prefix="ainovel:test:" + uuid4().hex)
        self.worker = Worker(self.runtime, self.queue)
        self.ids: list[UUID] = []
        self.story_id: UUID | None = None
        self.head: UUID | None = None
        self.auto_confirm = auto_confirm

    async def execute(
        self, payload: Request, base: UUID | None = None, story_id: UUID | None = None
    ) -> CommandView:
        if isinstance(payload, GenerateRequest) and payload.planned_scene_id is None:
            # Legacy scenarios still exercise writing after explicitly preparing a scene.
            from ainovel.core.domain import PlanSceneRequest
            from ainovel.service.scene_planning import next_scene

            source = (
                (await self.runtime.store.revision(base))
                if base
                else await self.runtime.store.head(story_id or self.story_id)
            )
            planned = next_scene(source.story)
            if planned is None:
                prepared = await self.execute(
                    PlanSceneRequest(
                        instruction=payload.instruction, parent_id=source.story.active_scene_leaf_id
                    ),
                    base=base,
                    story_id=story_id,
                )
                if prepared.status != "completed":
                    return prepared
                base = self.head
                source = await self.runtime.store.head(story_id or self.story_id)
                planned = next_scene(source.story)
            payload = payload.model_copy(update={"planned_scene_id": planned.id})
        standalone = payload.kind in ("interview", "create_story")
        submission = CommandSubmission(
            payload=payload,
            story_id=None if standalone else (story_id or self.story_id),
            base_revision_id=None if standalone else (base or self.head),
        )
        self.ids.append(submission.id)
        await self.runtime.store.submit(submission)
        await self.worker.process(submission.id)
        result = await self.runtime.store.command(submission.id)
        if self.auto_confirm and isinstance(payload, GenerateRequest) and result.output.writing_draft:
            result = await self.confirm_generation(submission.id)
        if result.output.revision_id:
            self.story_id = result.output.story_id
            self.head = result.output.revision_id
        return result

    async def confirm_generation(self, command_id: UUID) -> CommandView:
        parent = await self.runtime.store.command(command_id)
        draft = parent.output.writing_draft
        assert parent.status == "waiting" and draft is not None
        confirmed = await self.execute(
            ReviewDraftRequest(
                generation_command_id=command_id, expected_draft_version=draft.version, decision="confirm"
            )
        )
        if confirmed.status == "failed":
            return confirmed
        return await self.runtime.store.command(command_id)

    async def create(self) -> CommandView:
        return await self.execute(
            CreateStoryRequest(
                confirmed=True,
                proposal=CreationProposal(
                    title="集成测试-" + uuid4().hex[:8],
                    world=make_world("近未来都市"),
                    characters=[CharacterSpec(name="林初")],
                    preferences={"entries": [{"name": "偏好", "value": "克制；避免突然反转"}]},
                ),
            )
        )

    async def cleanup(self) -> None:
        async with self.runtime.store.sessions.begin() as session:
            commands = (await session.scalars(select(CommandRow).where(CommandRow.id.in_(self.ids)))).all()
            sessions = [row.payload.session_id for row in commands if row.payload.session_id]
            await session.execute(delete(SetupDraftRow).where(SetupDraftRow.id.in_(sessions)))
            if self.story_id:
                await session.execute(
                    delete(PromptTemplateBindingRow).where(PromptTemplateBindingRow.scope_id == self.story_id)
                )
                await session.execute(
                    delete(PromptTemplateVersionRow).where(PromptTemplateVersionRow.scope_id == self.story_id)
                )
                revision_ids = select(RevisionRow.id).where(RevisionRow.story_id == self.story_id)
                await session.execute(delete(ChunkRow).where(ChunkRow.revision_id.in_(revision_ids)))
                await session.execute(delete(FieldChangeRow).where(FieldChangeRow.story_id == self.story_id))
                await session.execute(
                    delete(FieldSnapshotRow).where(FieldSnapshotRow.story_id == self.story_id)
                )
                await session.execute(delete(RevisionRow).where(RevisionRow.story_id == self.story_id))
                await session.execute(delete(StoryRow).where(StoryRow.id == self.story_id))
            await session.execute(delete(EventRow).where(EventRow.command_id.in_(self.ids)))
            await session.execute(delete(FieldTemplateRow).where(FieldTemplateRow.command_id.in_(self.ids)))
            await session.execute(
                delete(FieldTemplateSetRow).where(FieldTemplateSetRow.command_id.in_(self.ids))
            )
            await session.execute(delete(OperationRow).where(OperationRow.command_id.in_(self.ids)))
            await session.execute(delete(CommandRow).where(CommandRow.id.in_(self.ids)))
        for command_id in self.ids:
            await self.queue.redis.lrem(self.queue.waiting, 0, str(command_id))
            await self.queue.redis.lrem(self.queue.processing, 0, str(command_id))
            await self.queue.redis.delete(self.queue.marker(command_id))
        await self.queue.close()
        await self.runtime.close()


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    harness = Harness()
    await harness.runtime.store.initialize()
    try:
        yield harness
    finally:
        await harness.cleanup()


async def test_interview_creation_generation_and_noncanon_chat(harness: Harness) -> None:
    turns: list[Turn] = []
    for answer in ["都市", "一个谨慎的女孩", "慢热，讨厌降智"]:
        turns.append(Turn(role="user", content=answer))
        result = await harness.execute(InterviewRequest(turns=list(turns)))
        assert result.status == "completed", result.error
        turns.append(Turn(role="assistant", content=result.output.interview.reply))
    assert result.output.interview.proposal is not None
    assert (await harness.create()).status == "completed"
    first = await harness.execute(GenerateRequest(instruction="从雨夜来信开始"))
    assert first.status == "completed", first.error
    first_revision = harness.head
    story1 = await harness.runtime.store.head(harness.story_id)
    first_snapshot = story1.story.snapshot(story1.story.characters[0].id)
    second = await harness.execute(GenerateRequest(instruction="继续"))
    assert second.status == "completed", second.error
    story2 = await harness.runtime.store.head(harness.story_id)
    assert len(story2.story.snapshot(story2.story.characters[0].id).experiences) == 2
    assert story2.story.snapshot(story2.story.characters[0].id, story1.story.scenes[0].id) == first_snapshot
    chat = await harness.execute(
        CharacterChatRequest(
            character_id=story1.story.characters[0].id,
            scene_id=story1.story.scenes[0].id,
            message="你此刻在想什么？",
        ),
        base=first_revision,
    )
    assert chat.status == "completed", chat.error
    assert chat.output.chat.snapshot_id == first_snapshot.id
    assert not chat.output.chat.canon
    assert (await harness.runtime.store.head(harness.story_id)).revision_id == story2.revision_id
    assert len(await harness.runtime.store.revisions(harness.story_id)) == 5


async def test_prompt_template_versions_pin_per_run_and_undo_restores_binding(harness: Harness) -> None:
    await harness.create()
    saved = await harness.execute(
        SavePromptTemplateRequest(
            scope="story", task="writing", name="克制对白", guidance="少用解释性独白，优先用动作表达情绪。"
        )
    )
    assert saved.status == "completed", saved.error
    version = saved.output.prompt_template
    assert version is not None and version.version == 1
    active = await harness.runtime.store.active_prompt_template(harness.story_id, "writing")
    assert active is not None and active.id == version.id

    run = await harness.execute(
        GenerateRequest(
            instruction="写一幕",
            prompt_templates=[PromptTemplateSelection(task="writing", version_id=version.id)],
        )
    )
    assert run.status == "completed", run.error
    checkpoint = await harness.runtime.store.checkpoint(run.submission.id)
    assert checkpoint is not None
    assert next(item for item in checkpoint.prompt_templates if item.task == "writing").id == version.id

    # Preparing a scene, generating and confirming prose are separate reversible operations.
    for _ in range(4):
        state = await UndoService(harness.runtime.store).state(harness.story_id)
        undo = await harness.execute(UndoRequest(expected_operation_id=state.operation_id))
        assert undo.status == "completed", undo.error
    assert await harness.runtime.store.active_prompt_template(harness.story_id, "writing") is None
    assert await harness.runtime.store.prompt_template_version(version.id) == version


async def test_preview_branch_revert_conflict_and_delivery_idempotency(harness: Harness) -> None:
    await harness.create()
    await harness.execute(GenerateRequest(instruction="第一幕"))
    await harness.execute(GenerateRequest(instruction="第二幕"))
    before = await harness.runtime.store.head(harness.story_id)
    direction = await harness.execute(
        DirectionRequest(direction=Direction(instruction="不要接吻", hard=True))
    )
    assert direction.status == "completed", direction.error
    assert len((await harness.runtime.store.head(harness.story_id)).story.scenes) == 2
    source = harness.head
    scene = before.story.scenes[0]
    selected = scene.text[:10]
    preview = await harness.execute(
        RewriteRequest(
            scene_id=scene.id,
            start=0,
            end=len(selected),
            selected_text=selected,
            instruction="用动作表现犹豫",
        )
    )
    assert preview.status == "completed", preview.error
    assert (await harness.runtime.store.head(harness.story_id)).revision_id == source
    accepted = await harness.execute(AcceptRewriteRequest(proposal_command_id=preview.submission.id))
    assert accepted.status == "completed", accepted.error
    branch = await harness.runtime.store.head(harness.story_id)
    assert len(branch.story.scenes) == 1
    assert selected in branch.story.scenes[0].text
    assert branch.story.scenes[0].text == preview.output.rewrite.replacement + scene.text[len(selected) :]
    assert len((await harness.runtime.store.revision(source)).story.scenes) == 2
    revision_count = len(await harness.runtime.store.revisions(harness.story_id))
    await harness.worker.process(accepted.submission.id)
    assert len(await harness.runtime.store.revisions(harness.story_id)) == revision_count
    conflict = await harness.execute(GenerateRequest(instruction="旧版本并发请求"), base=source)
    assert conflict.status == "failed" and "ConflictError" in conflict.error
    assert (await harness.runtime.store.head(harness.story_id)).revision_id == branch.revision_id
    reverted = await harness.execute(RevertRequest(target_revision_id=source))
    assert reverted.status == "completed", reverted.error
    restored = await harness.runtime.store.head(harness.story_id)
    assert len(restored.story.scenes) == 2
    chunks = await harness.runtime.retrieval.narrative(restored.revision_id, "来信", reverted.submission.id)
    assert chunks and all(chunk.chapter_id == restored.story.chapters[0].id for chunk in chunks)


async def test_writing_has_no_automatic_character_approval_or_extra_agents(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    harness.auto_confirm = False
    await harness.create()
    await harness.execute(GenerateRequest(instruction="第一幕"))
    first = await harness.runtime.store.head(harness.story_id)
    assert first.story.scenes == []
    assert len(first.story.characters) == 1
    generation = (await harness.runtime.store.commands(harness.story_id))[-1]
    await harness.confirm_generation(generation.submission.id)
    model = harness.runtime.services.model
    model.choose_agent_tool = AsyncMock(side_effect=model.choose_agent_tool)
    second = await harness.execute(GenerateRequest(instruction="引入新角色，但不能触发额外角色审批流程"))
    assert second.status == "waiting" and second.output.writing_draft is not None
    assert second.output.approval_phase is None and second.output.need is None
    # Scene planning and writing reuse the two explicit business agents only.
    assert {call.args[0].task for call in model.choose_agent_tool.call_args_list} == {
        "scene_planning",
        "writing",
    }
    count = model.choose_agent_tool.await_count
    confirmed = await harness.confirm_generation(second.submission.id)
    assert confirmed.status == "completed", confirmed.error
    assert model.choose_agent_tool.await_count == count
    after = await harness.runtime.store.head(harness.story_id)
    assert len(after.story.characters) == 1 and len(after.story.scenes) == 2


async def test_whole_chapter_edit_uses_the_writing_agent(harness: Harness) -> None:
    await harness.create()
    await harness.execute(GenerateRequest(instruction="第一幕"))
    await harness.execute(GenerateRequest(instruction="继续"))
    before = await harness.runtime.store.head(harness.story_id)
    text = "\n\n".join(scene.text for scene in before.story.scenes)
    proposal = await harness.execute(
        RewriteRequest(
            chapter_id=before.story.chapters[0].id,
            start=0,
            end=len(text),
            selected_text=text,
            instruction="修改整章文风",
        )
    )
    assert proposal.status == "completed", proposal.error
    accepted = await harness.execute(AcceptRewriteRequest(proposal_command_id=proposal.submission.id))
    assert accepted.status == "completed", accepted.error
    assert len((await harness.runtime.store.head(harness.story_id)).story.scenes) == 1


async def test_reindex_supports_embedding_dimension_change_without_changing_story(harness: Harness) -> None:
    from ainovel.llm.embeddings import HashEmbedder

    await harness.create()
    await harness.execute(GenerateRequest(instruction="第一幕"))
    before = await harness.runtime.store.head(harness.story_id)
    harness.runtime.retrieval.embedder = HashEmbedder(64)
    rebuilt = await harness.execute(ReindexRequest())
    assert rebuilt.status == "completed", rebuilt.error
    after = await harness.runtime.store.head(harness.story_id)
    assert after.story == before.story
    assert after.revision_id != before.revision_id
    recalled = await harness.runtime.retrieval.narrative(after.revision_id, "来信", rebuilt.submission.id)
    assert recalled


async def test_discarded_rewrite_is_audited_and_cannot_be_accepted(harness: Harness) -> None:
    await harness.create()
    await harness.execute(GenerateRequest(instruction="第一幕"))
    before = await harness.runtime.store.head(harness.story_id)
    scene = before.story.scenes[0]
    proposal = await harness.execute(
        RewriteRequest(scene_id=scene.id, start=0, end=5, selected_text=scene.text[:5], instruction="改写")
    )
    discarded = await harness.execute(RejectRewriteRequest(proposal_command_id=proposal.submission.id))
    assert discarded.status == "completed", discarded.error
    rejected = await harness.runtime.store.command(proposal.submission.id)
    assert rejected.output.rewrite.status == "rejected"
    attempted = await harness.execute(AcceptRewriteRequest(proposal_command_id=proposal.submission.id))
    assert attempted.status == "failed"
    assert (await harness.runtime.store.head(harness.story_id)).revision_id == before.revision_id


async def test_http_queue_sse_replay_and_command_id_deduplication(harness: Harness) -> None:
    app = create_app(harness.runtime.settings, job_queue=harness.queue)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = CommandSubmission(payload=InterviewRequest())
            harness.ids.append(payload.id)
            response = await client.post(
                "/api/commands",
                content=payload.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 202
            command_id = await harness.queue.receive()
            assert command_id == payload.id
            await harness.worker.process(command_id)
            await harness.queue.ack(command_id)
            response = await client.get(f"/api/commands/{payload.id}/stream")
            assert (
                response.status_code == 200
                and "run.completed" in response.text
                and "event: done" in response.text
            )
            events = await harness.runtime.store.events(payload.id)
            replay = await client.get(
                f"/api/commands/{payload.id}/stream", headers={"Last-Event-ID": str(events[-1].id)}
            )
            assert "run.completed" not in replay.text
            duplicate = await client.post(
                "/api/commands",
                content=payload.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            assert duplicate.status_code == 202
            assert CommandView.model_validate_json(duplicate.content).status == "completed"
            payload.payload = InterviewRequest(turns=[Turn(role="user", content="不同请求")])
            conflict = await client.post(
                "/api/commands",
                content=payload.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            assert conflict.status_code == 409
            health = await client.get("/health")
            assert health.status_code == 200


async def test_scene_settings_branches_and_ordered_writing(harness: Harness) -> None:
    from ainovel.core.domain import PlanSceneRequest, SelectSceneBranchRequest
    from ainovel.service.scene_planning import next_scene

    assert (await harness.create()).status == "completed"
    a = await harness.execute(PlanSceneRequest(instruction="第一幕：收到信"))
    assert a.status == "completed", a.error
    story = (await harness.runtime.store.head(harness.story_id)).story
    first = next_scene(story)
    assert first is not None and not story.scenes
    for instruction in ("第二幕：去码头", "第二幕分支：去邮局"):
        result = await harness.execute(PlanSceneRequest(parent_id=first.id, instruction=instruction))
        assert result.status == "completed", result.error
    story = (await harness.runtime.store.head(harness.story_id)).story
    assert len(story.planned_scenes) == 3 and not story.scenes
    alternate = story.active_scene_leaf_id
    draft = await harness.execute(GenerateRequest(instruction="依计划写正文", planned_scene_id=first.id))
    assert draft.status == "completed", draft.error
    story = (await harness.runtime.store.head(harness.story_id)).story
    assert story.scenes[0].id == first.id and next_scene(story).id == alternate
    locked = await harness.execute(PlanSceneRequest(target_id=first.id, instruction="改变第一幕设定"))
    assert locked.status == "failed" and "锁定" in locked.error
    # Failure did not change the head or create a new plan.
    story = (await harness.runtime.store.head(harness.story_id)).story
    assert len(story.planned_scenes) == 3 and len(story.scenes) == 1
    selected = await harness.execute(SelectSceneBranchRequest(leaf_id=story.planned_scenes[1].id))
    assert selected.status == "completed", selected.error
    story = (await harness.runtime.store.head(harness.story_id)).story
    assert next_scene(story).id == story.planned_scenes[1].id


async def test_planning_agent_batches_across_multiple_sessions(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    from ainovel.core.discussion import (
        DiscussionRequest,
        ReplyAction,
        SaveScenePlansAction,
        SaveScenePlansArguments,
        ScenePlanInput,
        ScenePlanUpdate,
        StartDiscussionRequest,
    )

    await harness.create()
    first_id, second_id, third_id = uuid4(), uuid4(), uuid4()
    model = harness.runtime.services.model
    original = model.choose_agent_tool
    session_a, session_b = uuid4(), uuid4()
    for session_id in (session_a, session_b):
        started = await harness.execute(StartDiscussionRequest(session_id=session_id, mode="planning"))
        assert started.status == "completed", started.error
    model.choose_agent_tool = AsyncMock(
        side_effect=[
            SaveScenePlansAction(
                arguments=SaveScenePlansArguments(
                    create=[
                        ScenePlanInput(id=first_id, title="来信", summary="收到一封信"),
                        ScenePlanInput(id=second_id, parent_id=first_id, title="码头", summary="去码头"),
                        ScenePlanInput(
                            id=third_id, parent_id=second_id, title="灯塔", summary="发现灯塔线索"
                        ),
                    ]
                )
            ),
            ReplyAction(reply="已规划三幕"),
            SaveScenePlansAction(
                arguments=SaveScenePlansArguments(
                    update=[
                        ScenePlanUpdate(id=second_id, summary="先观察码头守卫"),
                        ScenePlanUpdate(id=third_id, summary="跟随守卫去灯塔"),
                    ]
                )
            ),
            ReplyAction(reply="已修改后两幕"),
        ]
    )
    try:
        first = await harness.execute(
            DiscussionRequest(mode="planning", session_id=session_a, message="规划三幕")
        )
        assert first.status == "completed", first.error
        assert first.output.discussion.settings_changed
        second = await harness.execute(
            DiscussionRequest(mode="planning", session_id=session_b, message="修改后两幕")
        )
        assert second.status == "completed", second.error
        story = (await harness.runtime.store.head(harness.story_id)).story
        assert len(story.planned_scenes) == 3 and not story.scenes
        assert story.planned_scenes[1].plan.scene_plan == "先观察码头守卫"
        assert story.planned_scenes[2].plan.scene_plan == "跟随守卫去灯塔"
        assert (await harness.runtime.store.checkpoint(first.submission.id)) is None
        # A settings mutation uses the story undo stack, preserving cross-session order.
        state = await UndoService(harness.runtime.store).state(harness.story_id)
        assert state.command_id == second.submission.id
        undone = await harness.execute(
            UndoRequest(target_command_id=second.submission.id, expected_operation_id=state.operation_id)
        )
        assert undone.status == "completed", undone.error
        story = (await harness.runtime.store.head(harness.story_id)).story
        assert story.planned_scenes[1].plan.scene_plan == "去码头"
    finally:
        model.choose_agent_tool = original


async def test_planning_repeated_tool_stops_and_preserves_success(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    from ainovel.core.discussion import (
        DiscussionRequest,
        SaveScenePlansAction,
        SaveScenePlansArguments,
        ScenePlanInput,
        StartDiscussionRequest,
    )

    await harness.create()
    session_id = uuid4()
    await harness.execute(StartDiscussionRequest(mode="planning", session_id=session_id))
    action = SaveScenePlansAction(
        arguments=SaveScenePlansArguments(create=[ScenePlanInput(title="来信", summary="收到信")])
    )
    model = harness.runtime.services.model
    original = model.choose_agent_tool
    model.choose_agent_tool = AsyncMock(return_value=action)
    try:
        result = await harness.execute(
            DiscussionRequest(mode="planning", session_id=session_id, message="写一幕")
        )
        assert result.status == "completed", result.error
        assert result.output.discussion.settings_changed
        assert len((await harness.runtime.store.head(harness.story_id)).story.planned_scenes) == 1
        assert model.choose_agent_tool.await_count == 2
    finally:
        model.choose_agent_tool = original


async def test_prompt_context_template_is_saved_and_used_in_planning(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    from ainovel.core.discussion import DiscussionRequest, ReplyAction, StartDiscussionRequest
    from ainovel.llm.agent import build_agent_request

    await harness.create()
    source = "幕设定角色：{{ story_title }}"
    layout = "世界数据：{{ world_context }}<!-- section -->人物数据：{{ characters_context }}"
    saved = await harness.execute(
        SavePromptTemplateRequest(
            scope="story", task="scene_planning", name="分离布局", source=source, context_source=layout
        )
    )
    assert saved.status == "completed", saved.error
    assert saved.output.prompt_template.context_source == layout
    session_id = uuid4()
    await harness.execute(StartDiscussionRequest(session_id=session_id, mode="planning"))
    model = harness.runtime.services.model
    original = model.choose_agent_tool

    async def choose(context, command_id, **kwargs):
        body = build_agent_request(harness.runtime.settings, context, kwargs["prompt_template"]).body
        assert body.messages[0].content.startswith("幕设定角色：")
        assert body.messages[2].content.startswith("世界数据：")
        assert body.messages[3].content.startswith("人物数据：")
        return ReplyAction(reply="已按独立模板读取资料")

    model.choose_agent_tool = AsyncMock(side_effect=choose)
    try:
        result = await harness.execute(
            DiscussionRequest(session_id=session_id, mode="planning", message="讨论一下")
        )
        assert result.status == "completed", result.error
        pinned = await harness.runtime.store.pin_prompt_templates(result.submission.id, [])
        assert pinned[0].context_source == layout
    finally:
        model.choose_agent_tool = original


async def test_planning_reads_materials_on_demand_without_mutating_story(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    from ainovel.core.discussion import (
        DiscussionRequest,
        ReadStoryMaterialsAction,
        ReadStoryMaterialsArguments,
        ReplyAction,
        StartDiscussionRequest,
    )

    await harness.create()
    session_id = uuid4()
    await harness.execute(StartDiscussionRequest(mode="planning", session_id=session_id))
    head = harness.head
    model = harness.runtime.services.model
    original = model.choose_agent_tool
    model.choose_agent_tool = AsyncMock(
        side_effect=[
            ReadStoryMaterialsAction(arguments=ReadStoryMaterialsArguments(scope="world", limit=1)),
            ReplyAction(reply="已读取世界设定"),
        ]
    )
    try:
        result = await harness.execute(
            DiscussionRequest(mode="planning", session_id=session_id, message="读取世界设定")
        )
        assert result.status == "completed", result.error
        outcome = result.output.discussion.tool_results[0]
        assert outcome.name == "read_story_materials" and outcome.success
        assert outcome.materials[0].value == "近未来都市"
        assert not result.output.discussion.settings_changed
        assert (await harness.runtime.store.head(harness.story_id)).revision_id == head
    finally:
        model.choose_agent_tool = original


async def test_five_business_agents_and_no_model_call_on_confirmation(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    from ainovel.core.discussion import DiscussionRequest, StartDiscussionRequest
    from ainovel.core.domain import (
        AcceptRewriteRequest,
        CharacterChatRequest,
        RewriteRequest,
        StartCharacterChatRequest,
    )
    from ainovel.service.scene_planning import next_scene

    interview = await harness.execute(InterviewRequest(turns=[Turn(role="user", content="一个港口的故事")]))
    assert interview.status == "completed" and interview.output.interview.proposal
    await harness.create()
    await harness.execute(
        __import__("ainovel.core.domain", fromlist=["PlanSceneRequest"]).PlanSceneRequest(
            instruction="收到一封信"
        )
    )
    story = (await harness.runtime.store.head(harness.story_id)).story
    model = harness.runtime.services.model
    model.choose_agent_tool = AsyncMock(wraps=model.choose_agent_tool)
    harness.auto_confirm = False
    draft = await harness.execute(
        GenerateRequest(instruction="按计划写", planned_scene_id=next_scene(story).id)
    )
    assert draft.status == "waiting", draft.error
    assert not (await harness.runtime.store.head(harness.story_id)).story.scenes
    assert model.choose_agent_tool.await_count == 1
    confirmed = await harness.confirm_generation(draft.submission.id)
    assert confirmed.status == "completed", confirmed.error
    assert model.choose_agent_tool.await_count == 1
    head = await harness.runtime.store.head(harness.story_id)
    assert len(head.story.scenes) == 1
    session = uuid4()
    await harness.execute(StartDiscussionRequest(session_id=session))
    discussed = await harness.execute(DiscussionRequest(session_id=session, message="聊聊这个情节"))
    assert discussed.status == "completed", discussed.error
    assert (await harness.runtime.store.head(harness.story_id)).revision_id == head.revision_id
    character = head.story.characters[0]
    scene = head.story.scenes[0]
    started = await harness.execute(StartCharacterChatRequest(character_id=character.id, scene_id=scene.id))
    chatted = await harness.execute(
        CharacterChatRequest(
            character_id=character.id,
            scene_id=scene.id,
            session_id=started.output.chat_session_id,
            message="你在想什么",
        )
    )
    assert chatted.status == "completed" and not chatted.output.chat.canon, chatted.error
    rewritten = await harness.execute(
        RewriteRequest(
            scene_id=scene.id, start=0, end=4, selected_text=scene.text[:4], instruction="换个表达"
        )
    )
    assert rewritten.status == "completed", rewritten.error
    calls = model.choose_agent_tool.await_count
    accepted = await harness.execute(AcceptRewriteRequest(proposal_command_id=rewritten.submission.id))
    assert accepted.status == "completed", accepted.error
    assert model.choose_agent_tool.await_count == calls
    tasks = [call.args[0].task for call in model.choose_agent_tool.call_args_list]
    assert tasks == ["writing", "discussion", "character_chat", "writing"]


async def test_draft_revision_undo_and_worker_restart_need_no_graph(harness: Harness) -> None:
    from unittest.mock import AsyncMock

    from ainovel.core.domain import PlanSceneRequest, ReviewDraftRequest

    await harness.create()
    await harness.execute(PlanSceneRequest(instruction="来信"))
    harness.auto_confirm = False
    generated = await harness.execute(GenerateRequest(instruction="写正文"))
    assert generated.status == "waiting", generated.error
    revised = await harness.execute(
        ReviewDraftRequest(
            generation_command_id=generated.submission.id,
            expected_draft_version=1,
            decision="revise",
            feedback="精简对白",
            text_only=True,
        )
    )
    assert revised.status == "completed", revised.error
    assert revised.output.writing_draft.version == 2
    assert not (await harness.runtime.store.head(harness.story_id)).story.scenes
    state = await UndoService(harness.runtime.store).state(harness.story_id)
    undone = await harness.execute(
        UndoRequest(target_command_id=revised.submission.id, expected_operation_id=state.operation_id)
    )
    assert undone.status == "completed", undone.error
    parent = await harness.runtime.store.command(generated.submission.id)
    assert parent.output.writing_draft.version == 1
    settings = harness.runtime.settings
    await harness.runtime.close()
    harness.runtime = Runtime(settings)
    harness.worker = Worker(harness.runtime, harness.queue)
    harness.runtime.services.model.choose_agent_tool = AsyncMock(
        wraps=harness.runtime.services.model.choose_agent_tool
    )
    confirmed = await harness.confirm_generation(generated.submission.id)
    assert confirmed.status == "completed", confirmed.error
    assert len((await harness.runtime.store.head(harness.story_id)).story.scenes) == 1
    harness.runtime.services.model.choose_agent_tool.assert_not_awaited()
