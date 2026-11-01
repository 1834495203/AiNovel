"""Opt-in real PostgreSQL + pgvector + Redis tests. Only this fixture's records are deleted."""

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
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
    Direction,
    DirectionRequest,
    GenerateRequest,
    InterviewRequest,
    JudgeResult,
    PromptTemplateSelection,
    ReindexRequest,
    RejectRewriteRequest,
    Request,
    ResolveCharacterRequest,
    RevertRequest,
    ReviewDraftRequest,
    RewriteRequest,
    SavePromptTemplateRequest,
    StorySetup,
    Turn,
    UndoRequest,
)
from ainovel.infrastructure.job_queue import JobQueue
from ainovel.llm.mock import MockModel
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
                setup=StorySetup(
                    title="集成测试-" + uuid4().hex[:8],
                    world="近未来都市",
                    characters=[CharacterSpec(name="林初")],
                    preferences=["克制"],
                    dislikes=["突然反转"],
                ),
            )
        )

    async def cleanup(self) -> None:
        if self.runtime.services.workflow.checkpoints.ready:
            for command_id in self.ids:
                await self.runtime.services.workflow.checkpoints.saver.adelete_thread(str(command_id))
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
                await session.execute(delete(FieldSnapshotRow).where(FieldSnapshotRow.story_id == self.story_id))
                await session.execute(delete(RevisionRow).where(RevisionRow.story_id == self.story_id))
                await session.execute(delete(StoryRow).where(StoryRow.id == self.story_id))
            await session.execute(delete(EventRow).where(EventRow.command_id.in_(self.ids)))
            await session.execute(delete(FieldTemplateRow).where(FieldTemplateRow.command_id.in_(self.ids)))
            await session.execute(delete(FieldTemplateSetRow).where(FieldTemplateSetRow.command_id.in_(self.ids)))
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
    assert len(await harness.runtime.store.revisions(harness.story_id)) == 3


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

    # Waiting for draft and confirming it each create an undoable operation.
    for _ in range(3):
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
    assert "咽了回去" in branch.story.scenes[0].text
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


async def test_npc_two_phase_approval_resume_and_historical_visibility(harness: Harness) -> None:
    await harness.create()
    await harness.execute(GenerateRequest(instruction="第一幕"))
    before = await harness.runtime.store.head(harness.story_id)
    generation = await harness.execute(GenerateRequest(instruction="引入一个新角色 NPC"))
    assert generation.status == "waiting", generation.error
    assert generation.output.approval_phase == "consent"
    premature = await harness.execute(
        ResolveCharacterRequest(
            generation_command_id=generation.submission.id,
            decision="accept",
            character=CharacterSpec(name="周启明"),
        )
    )
    assert premature.status == "failed"
    consent = await harness.execute(
        ResolveCharacterRequest(generation_command_id=generation.submission.id, decision="consent")
    )
    assert consent.status == "completed", consent.error
    assert consent.output.approval_phase == "profile"
    assert (await harness.runtime.store.head(harness.story_id)).revision_id == before.revision_id
    accepted = await harness.execute(
        ResolveCharacterRequest(
            generation_command_id=generation.submission.id,
            decision="accept",
            character=consent.output.character,
        )
    )
    assert accepted.status == "completed", accepted.error
    await harness.worker.process(generation.submission.id)
    await harness.confirm_generation(generation.submission.id)
    completed = await harness.runtime.store.command(generation.submission.id)
    assert completed.status == "completed", completed.error
    after = await harness.runtime.store.head(harness.story_id)
    assert len(after.story.characters) == 2 and len(after.story.scenes) == 2
    npc = after.story.characters[-1]
    with pytest.raises(ValueError, match="尚未出现"):
        after.story.snapshot(npc.id, after.story.scenes[0].id)


async def test_rejected_npc_and_whole_chapter_edit(harness: Harness) -> None:
    await harness.create()
    generation = await harness.execute(GenerateRequest(instruction="引入新角色"))
    rejected = await harness.execute(
        ResolveCharacterRequest(generation_command_id=generation.submission.id, decision="reject")
    )
    assert rejected.status == "completed", rejected.error
    await harness.worker.process(generation.submission.id)
    await harness.confirm_generation(generation.submission.id)
    first = await harness.runtime.store.head(harness.story_id)
    harness.head = first.revision_id
    assert len(first.story.characters) == 1
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


class RejectingModel(MockModel):
    def _result(self, schema, context):
        if schema is JudgeResult:
            return JudgeResult(
                user_intent_satisfied=False,
                direction_satisfied=True,
                overall_score=0,
                issues=[],
                should_rewrite=True,
                rewrite_instructions=["仍不符合意图"],
            )
        return super()._result(schema, context)


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


async def test_judge_limit_keeps_failed_draft_out_of_canon(harness: Harness) -> None:
    await harness.create()
    before = harness.head
    harness.runtime.services.workflow.model = RejectingModel(harness.runtime.sink)
    result = await harness.execute(GenerateRequest(instruction="无法满足的要求"))
    assert result.status == "failed" and "重写上限" in result.error
    head = await harness.runtime.store.head(harness.story_id)
    assert head.revision_id == before and not head.story.scenes
    checkpoint = await harness.runtime.store.checkpoint(result.submission.id)
    assert checkpoint.draft is not None


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
