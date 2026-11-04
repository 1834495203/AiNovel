"""Explicit failure recovery, durable undo and resume without redoing completed AI steps."""

import os
from typing import Literal, TypeVar
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import delete
from test_integration import Harness

from ainovel.core.domain import (
    CharacterChatRequest,
    CommandSubmission,
    GenerateRequest,
    InterviewRequest,
    RecoverCommandRequest,
    ReviewDraftRequest,
    UndoRequest,
    UpdateStoryRequest,
)
from ainovel.core.ports import TraceSink
from ainovel.core.records import StoryMetadataPatch
from ainovel.llm.mock import MockModel
from ainovel.mapper.models import OperationRow
from ainovel.service.undo import UndoService

pytestmark = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")

T = TypeVar("T", bound=BaseModel)


class OnceFailingModel(MockModel):
    def __init__(self, sink: TraceSink, schema_name: str = "Draft") -> None:
        super().__init__(sink)
        self.schema_name = schema_name
        self.failed = False

    async def choose_agent_tool(self, context, command_id, *, prompt_template=None):
        task = {"Draft": "writing", "InterviewResult": "creation_interview", "ChatResult": "character_chat"}[
            self.schema_name
        ]
        if context.task == task and not self.failed:
            self.failed = True
            raise RuntimeError("simulated provider failure")
        return await super().choose_agent_tool(context, command_id, prompt_template=prompt_template)


def fail_once(harness: Harness, schema_name: str = "Draft") -> None:
    model = OnceFailingModel(harness.runtime.sink, schema_name)
    harness.runtime.services.model = model
    harness.runtime.services.writing_agent.model = model


async def request(
    harness: Harness, target_id: UUID, decision: Literal["retry", "withdraw"] = "retry"
) -> RecoverCommandRequest:
    events = await harness.runtime.store.events(target_id)
    return RecoverCommandRequest(
        target_command_id=target_id, expected_event_id=events[-1].id, decision=decision
    )


async def test_failed_generation_can_resume_and_every_recovery_can_be_undone() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        fail_once(h)
        failed = await h.execute(GenerateRequest(instruction="写第一幕"))
        original_head = failed.submission.base_revision_id
        assert failed.status == "failed" and h.head == original_head
        saved = await h.runtime.store.checkpoint(failed.submission.id)
        assert saved is not None and saved.plan is not None
        undo = await UndoService(h.runtime.store).state(h.story_id)
        assert undo.command_id == failed.submission.id
        resumed = await h.execute(await request(h, failed.submission.id))
        assert resumed.status == "completed", resumed.error
        assert (await h.runtime.store.command(failed.submission.id)).status == "queued"
        await h.worker.process(failed.submission.id)
        waiting = await h.runtime.store.command(failed.submission.id)
        assert waiting.status == "waiting" and waiting.output.writing_draft
        assert (await h.runtime.store.head(h.story_id)).revision_id == original_head
        events = await h.runtime.store.events(failed.submission.id)
        assert (
            sum(
                item.event.type == "step.started" and item.event.trace.node == "plan"
                for item in events
                if item.event.trace
            )
            == 0
        )
        for _ in range(2):
            undo = await UndoService(h.runtime.store).state(h.story_id)
            undone = await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
            assert undone.status == "completed", undone.error
        restored = await h.runtime.store.command(failed.submission.id)
        assert restored.status == "failed" and not restored.undone
        assert (
            restored.error == failed.error and await h.runtime.store.checkpoint(failed.submission.id) == saved
        )
        retried = await h.execute(await request(h, failed.submission.id))
        assert retried.status == "completed", retried.error
        await h.worker.process(failed.submission.id)
        await h.confirm_generation(failed.submission.id)
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
    finally:
        await h.cleanup()


async def test_queued_retry_can_be_undone_and_stale_recovery_cannot_queue_twice() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        fail_once(h)
        failed = await h.execute(GenerateRequest(instruction="第一幕"))
        retry_request = await request(h, failed.submission.id)
        assert (await h.execute(retry_request)).status == "completed"
        duplicate = await h.execute(retry_request)
        assert duplicate.status == "failed"
        undo = await UndoService(h.runtime.store).state(h.story_id)
        undone = await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
        assert undone.status == "completed", undone.error
        await h.worker.process(failed.submission.id)
        assert (await h.runtime.store.command(failed.submission.id)).status == "failed"
        stale = await h.execute(retry_request)
        assert stale.status == "failed" and "失败状态已变化" in stale.error
    finally:
        await h.cleanup()


async def test_old_failures_can_be_withdrawn_and_withdrawal_is_reversible() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        fail_once(h)
        failed = await h.execute(GenerateRequest(instruction="第一幕"))
        async with h.runtime.store.sessions.begin() as session:
            await session.execute(delete(OperationRow).where(OperationRow.command_id == failed.submission.id))
        recovered = await h.execute(await request(h, failed.submission.id, "withdraw"))
        assert recovered.status == "completed", recovered.error
        assert (await h.runtime.store.command(failed.submission.id)).undone
        undo = await UndoService(h.runtime.store).state(h.story_id)
        assert (await h.execute(UndoRequest(expected_operation_id=undo.operation_id))).status == "completed"
        assert not (await h.runtime.store.command(failed.submission.id)).undone
    finally:
        await h.cleanup()


async def test_changed_story_blocks_old_retry_but_allows_withdrawal() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        fail_once(h)
        failed = await h.execute(GenerateRequest(instruction="第一幕"))
        changed = await h.execute(UpdateStoryRequest(patch=StoryMetadataPatch(title="已修改")))
        assert changed.status == "completed"
        rejected = await h.execute(await request(h, failed.submission.id))
        assert rejected.status == "failed" and "版本已变化" in rejected.error
        assert (await h.runtime.store.head(h.story_id)).story.title == "已修改"
        withdrawn = await h.execute(await request(h, failed.submission.id, "withdraw"))
        assert withdrawn.status == "completed", withdrawn.error
    finally:
        await h.cleanup()


async def test_creation_session_failed_interview_can_be_retried_and_undone() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    scope = uuid4()
    try:
        fail_once(h, "InterviewResult")
        submission = CommandSubmission(session_id=scope, payload=InterviewRequest())
        h.ids.append(submission.id)
        await h.runtime.store.submit(submission)
        await h.worker.process(submission.id)
        assert (await h.runtime.store.command(submission.id)).status == "failed"
        retry = CommandSubmission(session_id=scope, payload=await request(h, submission.id))
        h.ids.append(retry.id)
        await h.runtime.store.submit(retry)
        await h.worker.process(retry.id)
        assert (await h.runtime.store.command(retry.id)).status == "completed"
        await h.worker.process(submission.id)
        assert (await h.runtime.store.command(submission.id)).output.interview
        undo = await UndoService(h.runtime.store).state(scope)
        assert undo.command_id == submission.id
    finally:
        await h.cleanup()


async def test_failed_database_confirmation_retries_without_a_model_call(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        generated = await h.execute(GenerateRequest(instruction="第一幕"))
        pending = generated.output.writing_draft
        model = h.runtime.services.model
        model.choose_agent_tool = AsyncMock(side_effect=model.choose_agent_tool)
        commit = h.runtime.services.commit
        calls = 0

        async def fail_database_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated database failure")
            return await commit(*args, **kwargs)

        monkeypatch.setattr(h.runtime.services, "commit", fail_database_once)
        failed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=generated.submission.id,
                expected_draft_version=pending.version,
                decision="confirm",
            )
        )
        assert failed.status == "failed"
        assert (await h.runtime.store.head(h.story_id)).story.scenes == []
        retry = await h.execute(await request(h, failed.submission.id))
        assert retry.status == "completed", retry.error
        await h.worker.process(failed.submission.id)
        completed = await h.runtime.store.command(failed.submission.id)
        assert completed.status == "completed", completed.error
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
        assert model.choose_agent_tool.await_count == 0
    finally:
        await h.cleanup()


async def test_character_chat_retry_keeps_its_original_stage_after_story_changes() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        await h.execute(GenerateRequest(instruction="第一幕"))
        original = await h.runtime.store.head(h.story_id)
        character = original.story.characters[0]
        scene = original.story.scenes[0]
        snapshot = original.story.snapshot(character.id, scene.id)
        fail_once(h, "ChatResult")
        failed = await h.execute(
            CharacterChatRequest(
                character_id=character.id, scene_id=scene.id, session_id=uuid4(), message="你怎么看这封信？"
            )
        )
        assert failed.status == "failed"
        changed = await h.execute(UpdateStoryRequest(patch=StoryMetadataPatch(title="后来修改的小说")))
        assert changed.status == "completed"
        retry = await h.execute(await request(h, failed.submission.id))
        assert retry.status == "completed", retry.error
        await h.worker.process(failed.submission.id)
        finished = await h.runtime.store.command(failed.submission.id)
        assert finished.status == "completed", finished.error
        assert finished.output.chat.snapshot_id == snapshot.id
        assert (await h.runtime.store.head(h.story_id)).revision_id == changed.output.revision_id
    finally:
        await h.cleanup()
