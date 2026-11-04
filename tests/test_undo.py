"""Undo tests exercise durable command effects, retries, workflow approval and concurrent clients."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_integration import Harness

from ainovel.core.domain import (
    AcceptRewriteRequest,
    CharacterChatRequest,
    CommandSubmission,
    DeleteStoryRequest,
    Direction,
    DirectionRequest,
    GenerateRequest,
    InterviewRequest,
    ReindexRequest,
    RejectRewriteRequest,
    RestoreStoryRequest,
    RevertRequest,
    RewriteRequest,
    StartCharacterChatRequest,
    Turn,
    UndoRequest,
)
from ainovel.core.errors import StoryUnavailableError
from ainovel.mapper.models import OperationRow
from ainovel.service.undo import UndoService

pytestmark = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")


async def undo(harness: Harness, scope=None):
    state = await UndoService(harness.runtime.store).state(scope or harness.story_id)
    assert state.available
    result = await harness.execute(UndoRequest(expected_operation_id=state.operation_id, scope_id=scope))
    assert result.status == "completed", result.error
    return result


async def test_continuous_undo_restores_story_chat_proposals_and_creation() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        await h.execute(GenerateRequest(instruction="第一幕"))
        generated = await h.runtime.store.head(h.story_id)
        scene = generated.story.scenes[0]
        await h.execute(DirectionRequest(direction=Direction(instruction="保持克制")))
        directed = h.head
        session_id = uuid4()
        empty = await h.execute(
            StartCharacterChatRequest(
                character_id=generated.story.characters[0].id, scene_id=scene.id, session_id=session_id
            )
        )
        assert empty.output.chat_session_id == session_id
        await undo(h, session_id)
        assert (await h.runtime.store.command(empty.submission.id)).undone
        chat = await h.execute(
            CharacterChatRequest(
                character_id=generated.story.characters[0].id,
                scene_id=scene.id,
                session_id=session_id,
                message="你在想什么",
            )
        )
        await undo(h, session_id)
        assert (await h.runtime.store.command(chat.submission.id)).undone
        assert not await h.runtime.store.chat_history(chat.submission)
        assert (await h.runtime.store.head(h.story_id)).revision_id == directed
        proposal = await h.execute(
            RewriteRequest(
                scene_id=scene.id, start=0, end=5, selected_text=scene.text[:5], instruction="改写"
            )
        )
        await h.execute(RejectRewriteRequest(proposal_command_id=proposal.submission.id))
        await undo(h)
        assert (await h.runtime.store.command(proposal.submission.id)).output.rewrite.status == "proposed"
        accepted = await h.execute(AcceptRewriteRequest(proposal_command_id=proposal.submission.id))
        assert accepted.status == "completed", accepted.error
        await undo(h)
        assert (await h.runtime.store.head(h.story_id)).revision_id == directed
        assert (await h.runtime.store.command(proposal.submission.id)).output.rewrite.status == "proposed"
        await undo(h)
        assert (await h.runtime.store.command(proposal.submission.id)).undone
        await undo(h)
        assert (await h.runtime.store.head(h.story_id)).story == generated.story
        await undo(h)
        assert not (await h.runtime.store.head(h.story_id)).story.scenes
        assert await h.runtime.store.revisions(h.story_id)  # Historical artifacts survive.
        await undo(h)  # Remove the waiting draft after undoing its confirmation.
        await undo(h)  # Remove the independently prepared scene.
        assert h.head == initial
        await undo(h)
        with pytest.raises(StoryUnavailableError):
            await h.runtime.store.head(h.story_id)
        assert not (await UndoService(h.runtime.store).state(h.story_id)).available
    finally:
        await h.cleanup()


async def test_delete_and_discard_undo_restore_the_pending_draft() -> None:
    from ainovel.core.domain import ReviewDraftRequest

    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        generation = await h.execute(GenerateRequest(instruction="写下一幕"))
        await h.execute(DeleteStoryRequest())
        assert (await h.runtime.store.command(generation.submission.id)).status == "failed"
        await undo(h)
        parent = await h.runtime.store.command(generation.submission.id)
        assert parent.status == "waiting" and parent.output.writing_draft is not None
        discarded = await h.execute(
            ReviewDraftRequest(
                generation_command_id=generation.submission.id,
                expected_draft_version=parent.output.writing_draft.version,
                decision="discard",
            )
        )
        assert discarded.status == "completed", discarded.error
        await undo(h)
        parent = await h.runtime.store.command(generation.submission.id)
        assert parent.status == "waiting" and not parent.undone
        assert parent.output.writing_draft.status == "pending"
        await h.worker.process(generation.submission.id)
        assert (await h.runtime.store.command(generation.submission.id)).status == "waiting"
    finally:
        await h.cleanup()


async def test_failed_commands_are_undoable_and_pending_work_blocks_undo() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        await h.execute(GenerateRequest(instruction="第一幕"))
        state = await UndoService(h.runtime.store).state(h.story_id)
        failed = await h.execute(
            DirectionRequest(direction=Direction(instruction="invalid", chapter_id=uuid4()))
        )
        assert failed.status == "failed"
        failure_state = await UndoService(h.runtime.store).state(h.story_id)
        assert (
            failure_state.command_id == failed.submission.id
            and failure_state.operation_id != state.operation_id
        )
        pending = CommandSubmission(
            story_id=h.story_id,
            base_revision_id=h.head,
            payload=DirectionRequest(direction=Direction(instruction="future")),
        )
        h.ids.append(pending.id)
        await h.runtime.store.submit(pending)
        blocked = await h.execute(UndoRequest(expected_operation_id=failure_state.operation_id))
        assert blocked.status == "failed" and "尚未完成" in blocked.error
        assert (await h.runtime.store.head(h.story_id)).revision_id != initial
        await h.runtime.store.fail(pending.id, "test cleanup")
        await undo(h)
        assert (await h.runtime.store.command(pending.id)).undone
        await undo(h)
        assert (await h.runtime.store.command(failed.submission.id)).undone
        await undo(h)  # Confirmed prose.
        await undo(h)  # Pending draft.
        await undo(h)  # Prepared scene.
        assert h.head == initial
    finally:
        await h.cleanup()


async def test_delete_restore_revert_and_index_are_sequentially_undoable() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        await h.execute(GenerateRequest(instruction="第一幕"))
        generated = h.head
        await h.execute(ReindexRequest())
        await undo(h)
        assert h.head == generated
        await h.execute(RevertRequest(target_revision_id=initial))
        await undo(h)
        assert h.head == generated
        await h.execute(DeleteStoryRequest())
        await h.execute(RestoreStoryRequest())
        await undo(h)
        with pytest.raises(StoryUnavailableError):
            await h.runtime.store.head(h.story_id)
        await undo(h)
        assert (await h.runtime.store.head(h.story_id)).revision_id == generated
        assert await h.runtime.retrieval.narrative(generated, "来信", h.ids[-1])
    finally:
        await h.cleanup()


async def test_session_interview_undo_and_retries_leave_immutable_audit() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    session_id = uuid4()
    try:

        async def execute(payload):
            submission = CommandSubmission(session_id=session_id, payload=payload)
            h.ids.append(submission.id)
            await h.runtime.store.submit(submission)
            await h.worker.process(submission.id)
            return await h.runtime.store.command(submission.id)

        first = await execute(InterviewRequest(turns=[]))
        second = await execute(InterviewRequest(turns=[Turn(role="user", content="近未来都市")]))
        for target in [second, first]:
            state = await UndoService(h.runtime.store).state(session_id)
            result = await execute(UndoRequest(expected_operation_id=state.operation_id))
            assert result.status == "completed", result.error
            await h.worker.process(result.submission.id)  # Delivery retry must not pop another frame.
            assert (await h.runtime.store.command(target.submission.id)).undone
            assert (await h.runtime.store.command(target.submission.id)).output.interview
        assert not (await UndoService(h.runtime.store).state(session_id)).available
        assert all(
            item.undone
            for item in await h.runtime.store.session_commands(session_id)
            if item.submission.payload.kind == "interview"
        )
    finally:
        await h.cleanup()


async def test_stale_and_concurrent_undo_cannot_pop_two_operations() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = h.head
        await h.execute(DirectionRequest(direction=Direction(instruction="慢热")))
        state = await UndoService(h.runtime.store).state(h.story_id)
        submissions = [
            CommandSubmission(
                story_id=h.story_id,
                base_revision_id=h.head,
                payload=UndoRequest(expected_operation_id=state.operation_id),
            )
            for _ in range(2)
        ]
        for submission in submissions:
            h.ids.append(submission.id)
            await h.runtime.store.submit(submission)
        await asyncio.gather(*(h.worker.process(item.id) for item in submissions))
        results = [await h.runtime.store.command(item.id) for item in submissions]
        assert sorted(item.status for item in results) == ["completed", "failed"]
        assert (await h.runtime.store.head(h.story_id)).revision_id == initial
        async with h.runtime.store.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OperationRow)
                    .where(OperationRow.scope_id == h.story_id, OperationRow.undone_by.is_(None))
                )
                == 1
            )
    finally:
        await h.cleanup()
