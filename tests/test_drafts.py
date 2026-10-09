"""Durable setup recovery and targeted sequential undo, using disposable synthetic sessions."""

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update
from test_integration import Harness

from ainovel.bootstrap.runtime import Runtime
from ainovel.controller.api import create_app
from ainovel.core.config import Settings
from ainovel.core.domain import (
    CharacterChatRequest,
    CommandSubmission,
    CreateStoryRequest,
    Direction,
    DirectionRequest,
    DraftFieldEdit,
    GenerateRequest,
    InterviewRequest,
    ResolveCharacterRequest,
    SaveSetupDraftRequest,
    SetupDraft,
    StorySetup,
    Turn,
    UndoRequest,
)
from ainovel.mapper.drafts import DraftRepository
from ainovel.mapper.models import CommandRow
from ainovel.service.undo import UndoService

pytestmark = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")


async def execute_session(h: Harness, session_id, payload):
    submission = CommandSubmission(session_id=session_id, payload=payload)
    h.ids.append(submission.id)
    await h.runtime.store.submit(submission)
    await h.worker.process(submission.id)
    result = await h.runtime.store.command(submission.id)
    assert result.status == "completed", result.error
    return result


async def test_draft_recovers_dialogue_input_and_incomplete_edits_after_new_runtime() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    session_id = uuid4()
    try:
        first = await execute_session(h, session_id, InterviewRequest(turns=[]))
        draft = SetupDraft(
            id=session_id,
            title="正在构思的故事",
            input_text="未发送的下一句话",
            pending_input="正在提交的输入",
            setup=StorySetup(title="原设定", world="近未来"),
            editing_setup=True,
            edits=[
                DraftFieldEdit(key="title", value=""),
                DraftFieldEdit(key="world", value="尚未写完的世界"),
            ],
        )
        await execute_session(h, session_id, SaveSetupDraftRequest(draft=draft))
        restarted = Runtime(Settings(llm_provider="mock"))
        try:
            recovered = await DraftRepository(restarted.store).get(session_id)
            assert recovered.input_text == draft.input_text and recovered.edits == draft.edits
            assert recovered.pending_input == draft.pending_input
            assert recovered.editing_setup and recovered.proposal == draft.proposal
            commands = await restarted.store.session_commands(session_id)
            assert any(
                item.submission.id == first.submission.id and item.output.interview for item in commands
            )
        finally:
            await restarted.close()
        app = create_app(Settings(llm_provider="mock"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/drafts/{session_id}")
                assert response.status_code == 200
                assert SetupDraft.model_validate_json(response.text).input_text == draft.input_text
        state = await UndoService(h.runtime.store).state(session_id)
        assert state.command_ids == [first.submission.id]  # Autosave does not pollute dialogue undo.
    finally:
        await h.cleanup()


@pytest.mark.parametrize("equal_timestamps", [False, True])
async def test_older_autosave_delivery_cannot_replace_newer_draft(equal_timestamps: bool) -> None:
    h = Harness()
    await h.runtime.store.initialize()
    session_id = uuid4()
    try:
        submissions = [
            CommandSubmission(
                session_id=session_id,
                payload=SaveSetupDraftRequest(draft=SetupDraft(id=session_id, input_text=text)),
            )
            for text in ["旧输入", "新输入"]
        ]
        for submission in submissions:
            h.ids.append(submission.id)
            await h.runtime.store.submit(submission)
        if equal_timestamps:
            first = await h.runtime.store.command(submissions[0].id)
            async with h.runtime.store.sessions.begin() as session:
                await session.execute(
                    update(CommandRow)
                    .where(CommandRow.id == submissions[1].id)
                    .values(created_at=first.created_at)
                )
        for submission in reversed(submissions):
            await h.worker.process(submission.id)
        assert (await DraftRepository(h.runtime.store).get(session_id)).input_text == "新输入"
    finally:
        await h.cleanup()


async def test_existing_interview_is_discoverable_and_creation_archives_draft() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    session_id = uuid4()
    try:
        await execute_session(h, session_id, InterviewRequest(turns=[Turn(role="user", content="都市悬疑")]))
        repository = DraftRepository(h.runtime.store)
        assert (await repository.get(session_id)).id == session_id  # No prior draft autosave needed.
        created = await execute_session(
            h,
            session_id,
            CreateStoryRequest(confirmed=True, setup=StorySetup(title="合成草稿", world="都市")),
        )
        h.story_id = created.output.story_id
        h.head = created.output.revision_id
        assert not any(item.id == session_id for item in await repository.list())
        state = await UndoService(h.runtime.store).state(h.story_id)
        assert (await h.execute(UndoRequest(expected_operation_id=state.operation_id))).status == "completed"
        assert (await repository.get(session_id)).id == session_id
    finally:
        await h.cleanup()


async def test_sequential_undo_rejects_older_target_and_restores_later_effects_one_at_a_time() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        first = await h.execute(GenerateRequest(instruction="第一轮"))
        await h.execute(DirectionRequest(direction=Direction(instruction="克制")))
        await h.execute(GenerateRequest(instruction="第二轮"))
        story = (await h.runtime.store.head(h.story_id)).story
        session_id = uuid4()
        for message in ["你在想什么", "你接下来怎么做"]:
            await h.execute(
                CharacterChatRequest(
                    character_id=story.characters[0].id,
                    scene_id=story.scenes[0].id,
                    session_id=session_id,
                    message=message,
                )
            )
        state = await UndoService(h.runtime.store).state(h.story_id)
        invalid = await h.execute(
            UndoRequest(expected_operation_id=state.operation_id, target_command_id=uuid4())
        )
        assert invalid.status == "failed" and h.head != initial
        rejected = await h.execute(
            UndoRequest(expected_operation_id=state.operation_id, target_command_id=first.submission.id)
        )
        assert rejected.status == "failed" and "只能撤回最新操作" in rejected.error
        for _ in range(7):
            state = await UndoService(h.runtime.store).state(h.story_id)
            result = await h.execute(
                UndoRequest(expected_operation_id=state.operation_id, target_command_id=state.command_id)
            )
            assert result.status == "completed", result.error
            assert len(result.output.undone_command_ids) == 1
        assert (await h.runtime.store.head(h.story_id)).revision_id == initial
        assert not (await h.runtime.store.head(h.story_id)).story.scenes
        assert len(await h.runtime.store.revisions(h.story_id)) >= 4
        assert (await UndoService(h.runtime.store).state(h.story_id)).kind == "create_story"
    finally:
        await h.cleanup()


async def test_sequential_generation_undo_restores_each_approval_frame() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        generation = await h.execute(GenerateRequest(instruction="引入一个新角色 NPC"))
        consent = await h.execute(
            ResolveCharacterRequest(generation_command_id=generation.submission.id, decision="consent")
        )
        await h.execute(
            ResolveCharacterRequest(
                generation_command_id=generation.submission.id,
                decision="accept",
                character=consent.output.character,
            )
        )
        await h.worker.process(generation.submission.id)
        h.head = (await h.runtime.store.head(h.story_id)).revision_id
        state = await UndoService(h.runtime.store).state(h.story_id)
        for _ in range(4):
            state = await UndoService(h.runtime.store).state(h.story_id)
            result = await h.execute(
                UndoRequest(expected_operation_id=state.operation_id, target_command_id=state.command_id)
            )
            assert result.status == "completed", result.error
            assert len(result.output.undone_command_ids) == 1
        assert h.head == initial
        parent = await h.runtime.store.command(generation.submission.id)
        assert parent.undone and parent.status == "failed"
        await h.worker.process(generation.submission.id)
        assert (await h.runtime.store.head(h.story_id)).revision_id == initial
    finally:
        await h.cleanup()


async def test_round_undo_clears_future_setup_edits_and_rejects_late_autosave() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    session_id = uuid4()
    try:
        first = await execute_session(h, session_id, InterviewRequest(turns=[]))
        draft = SetupDraft(
            id=session_id,
            input_text="保留未发送的输入",
            setup=StorySetup(title="后续设定", world="未来"),
            editing_setup=True,
            edits=[DraftFieldEdit(key="world", value="未来内容")],
        )
        await execute_session(h, session_id, SaveSetupDraftRequest(draft=draft))
        late = CommandSubmission(session_id=session_id, payload=SaveSetupDraftRequest(draft=draft))
        h.ids.append(late.id)
        await h.runtime.store.submit(late)
        state = await UndoService(h.runtime.store).state(session_id)
        result = await execute_session(
            h,
            session_id,
            UndoRequest(expected_operation_id=state.operation_id, target_command_id=first.submission.id),
        )
        assert result.status == "completed"
        await h.worker.process(late.id)
        restored = await DraftRepository(h.runtime.store).get(session_id)
        assert restored.proposal is None and not restored.edits and not restored.editing_setup
        assert restored.input_text == draft.input_text
    finally:
        await h.cleanup()
