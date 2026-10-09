"""Draft gates isolate speculative prose from canonical facts and support durable undo."""

import os
from typing import TypeVar
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from test_integration import Harness

from ainovel.bootstrap.runtime import Runtime
from ainovel.core.domain import (
    ContextChunk,
    Direction,
    DirectionRequest,
    DraftSelection,
    GenerateRequest,
    ResolveCharacterRequest,
    ReviewDraftRequest,
    Story,
    UndoRequest,
)
from ainovel.llm.mock import MockModel
from ainovel.llm.schemas import FinalizationResult
from ainovel.service.undo import UndoService
from ainovel.service.worker import Worker

pytestmark = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")
T = TypeVar("T", bound=BaseModel)


async def test_draft_gate_survives_restart_and_commits_only_after_confirmation() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        before = await h.runtime.store.head(h.story_id)
        draft = await h.execute(GenerateRequest(instruction="第一幕"))
        assert draft.status == "waiting", draft.error
        assert draft.output.writing_draft.version == 1
        assert await h.runtime.store.head(h.story_id) == before
        state = await h.runtime.store.checkpoint(draft.submission.id)
        assert state.extraction is None and state.maintenance is None
        snapshot = await h.runtime.services.workflow.graph.aget_state(
            {"configurable": {"thread_id": str(draft.submission.id)}}
        )
        assert snapshot.next == ("review",) and snapshot.tasks[0].interrupts
        settings = h.runtime.settings
        await h.runtime.close()
        h.runtime = Runtime(settings)
        h.worker = Worker(h.runtime, h.queue)
        confirmed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=draft.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert confirmed.status == "completed", confirmed.error
        after = await h.runtime.store.head(h.story_id)
        assert after.story.scenes[0].text == draft.output.writing_draft.content.text
        assert after.story.writer.mainlines and after.story.writer.notes
        assert after.story.scenes[0].character_patches
        assert (await h.runtime.store.command(draft.submission.id)).status == "completed"
        undo = await UndoService(h.runtime.store).state(h.story_id)
        undone = await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
        assert undone.status == "completed", undone.error
        assert await h.runtime.store.head(h.story_id) == before
        restored = await h.runtime.store.command(draft.submission.id)
        assert restored.status == "waiting" and restored.output.writing_draft.version == 1
        await h.confirm_generation(draft.submission.id)
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
    finally:
        await h.cleanup()


async def test_selected_draft_revision_preserves_surroundings_and_undo_restores_gate() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        baseline = await h.runtime.store.head(h.story_id)
        pending = await h.execute(GenerateRequest(instruction="第一幕"))
        text = pending.output.writing_draft.content.text
        selection = DraftSelection(start=5, end=12, selected_text=text[5:12])
        edited = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id,
                expected_draft_version=1,
                decision="revise",
                feedback="更克制一些",
                selection=selection,
            )
        )
        assert edited.status == "completed", edited.error
        assert edited.output.writing_draft.version == 2
        revised = edited.output.writing_draft.content.text
        assert revised.startswith(text[:5]) and revised.endswith(text[12:]) and revised != text
        assert await h.runtime.store.head(h.story_id) == baseline
        stale = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert stale.status == "failed" and "新版本" in stale.error
        undo = await UndoService(h.runtime.store).state(h.story_id)
        result = await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
        assert result.status == "completed" and (await h.runtime.store.command(stale.submission.id)).undone
        assert (await h.runtime.store.command(pending.submission.id)).output.writing_draft.version == 2
        undo = await UndoService(h.runtime.store).state(h.story_id)
        result = await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
        assert result.status == "completed", result.error
        restored = await h.runtime.store.command(pending.submission.id)
        assert (
            restored.output.writing_draft.content.text == text and restored.output.writing_draft.version == 1
        )
        edited_again = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id,
                expected_draft_version=1,
                decision="revise",
                feedback="换一种表达",
            )
        )
        assert edited_again.status == "completed" and edited_again.output.writing_draft.version == 2, (
            edited_again.error
        )
        unchanged_prefix = text.rsplit("\n\n", 1)[0] + "\n\n"
        assert edited_again.output.writing_draft.content.text.startswith(unchanged_prefix)
        assert edited_again.output.writing_draft.content.text != text
        discarded = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=2, decision="discard"
            )
        )
        assert discarded.status == "completed", discarded.error
        assert await h.runtime.store.head(h.story_id) == baseline
        undo = await UndoService(h.runtime.store).state(h.story_id)
        await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
        await h.confirm_generation(pending.submission.id)
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
    finally:
        await h.cleanup()


async def test_recall_and_character_subgraphs_can_both_be_skipped() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        result = await h.execute(GenerateRequest(instruction="继续"))
        assert result.status == "waiting", result.error
        events = await h.runtime.store.events(result.submission.id)
        steps = [
            item.event.trace.node for item in events if item.event.trace and item.event.type == "step.started"
        ]
        assert "skip_retrieval" in steps and "skip_characters" in steps
        assert "extract" not in steps and "maintain" not in steps
        nodes = h.runtime.services.workflow.graph.nodes
        for name in ("planning", "recall", "characters", "writing", "finalize"):
            assert nodes[name].bound.get_graph()
        invalid = await h.execute(
            ReviewDraftRequest(
                generation_command_id=result.submission.id,
                expected_draft_version=1,
                decision="revise",
                feedback="改写",
                selection=DraftSelection(start=0, end=5, selected_text="旧稿不匹配"),
            )
        )
        assert invalid.status == "failed" and "选区" in invalid.error
        foreign = await h.execute(
            ReviewDraftRequest(generation_command_id=uuid4(), expected_draft_version=1, decision="confirm")
        )
        assert foreign.status == "failed"
    finally:
        await h.cleanup()


async def test_confirmation_failure_leaves_draft_and_canonical_state_unchanged() -> None:
    class FailingFinalization(MockModel):
        async def generate(self, schema: type[T], task: str, context: BaseModel, command_id: UUID) -> T:
            if schema is FinalizationResult:
                raise ValueError("模拟确认后事实提取与维护失败")
            return await super().generate(schema, task, context, command_id)

    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        before = await h.runtime.store.head(h.story_id)
        pending = await h.execute(GenerateRequest(instruction="第一幕"))
        original = await h.runtime.store.command(pending.submission.id)
        h.runtime.services.workflow.model = FailingFinalization(h.runtime.model.sink)
        failed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert failed.status == "failed" and "模拟确认后事实提取与维护失败" in failed.error
        assert await h.runtime.store.head(h.story_id) == before
        assert await h.runtime.store.command(pending.submission.id) == original
        h.runtime.services.workflow.model = h.runtime.model
        confirmed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert confirmed.status == "completed", confirmed.error
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
        events = await h.runtime.store.events(confirmed.submission.id)
        assert any(item.event.trace and item.event.trace.node == "extract" for item in events)
        assert any(
            item.event.type == "llm.completed" and item.event.trace.node == "FinalizationResult"
            for item in events
        )
        await h.worker.process(confirmed.submission.id)
        assert await h.runtime.store.events(confirmed.submission.id) == events
        duplicate = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert duplicate.status == "failed"
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
    finally:
        await h.cleanup()


async def test_new_confirmation_reuses_maintenance_after_commit_failure(monkeypatch) -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        pending = await h.execute(GenerateRequest(instruction="第一幕"))
        original_index = h.runtime.services.retrieval.index

        async def broken_index(
            story: Story, command_id: UUID
        ) -> tuple[list[ContextChunk], list[list[float]]]:
            raise RuntimeError("模拟索引失败")

        monkeypatch.setattr(h.runtime.services.retrieval, "index", broken_index)
        failed = await h.confirm_generation(pending.submission.id)
        assert failed.status == "failed" and "索引失败" in failed.error
        assert (await h.runtime.store.head(h.story_id)).story.scenes == []
        monkeypatch.setattr(h.runtime.services.retrieval, "index", original_index)
        confirmed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert confirmed.status == "completed", confirmed.error
        events = await h.runtime.store.events(confirmed.submission.id)
        assert not any(item.event.type.startswith("llm.") for item in events)
        nodes = [item.event.trace.node for item in events if item.event.trace]
        assert "reuse_extract" in nodes and "reuse_maintain" in nodes
        assert len((await h.runtime.store.head(h.story_id)).story.scenes) == 1
    finally:
        await h.cleanup()


async def test_undone_failed_confirmation_is_not_reused() -> None:
    from test_recovery import fail_once

    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        pending = await h.execute(GenerateRequest(instruction="第一幕"))
        fail_once(h, "FinalizationResult")
        failed = await h.confirm_generation(pending.submission.id)
        assert failed.status == "failed"
        undo = await UndoService(h.runtime.store).state(h.story_id)
        await h.execute(UndoRequest(expected_operation_id=undo.operation_id))
        confirmed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            )
        )
        assert confirmed.status == "completed", confirmed.error
        events = await h.runtime.store.events(confirmed.submission.id)
        assert any(
            item.event.type == "llm.completed" and item.event.trace.node == "FinalizationResult"
            for item in events
        )
        assert not any(item.event.trace and item.event.trace.node == "reuse_confirmation" for item in events)
    finally:
        await h.cleanup()


async def test_stale_canonical_head_blocks_confirmation_but_allows_discard() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        source = h.head
        pending = await h.execute(GenerateRequest(instruction="第一幕"))
        await h.execute(DirectionRequest(direction=Direction(instruction="变更全局要求")))
        latest = await h.runtime.store.head(h.story_id)
        confirmed = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="confirm"
            ),
            base=source,
        )
        assert confirmed.status == "failed" and "已变化" in confirmed.error
        discarded = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id, expected_draft_version=1, decision="discard"
            ),
            base=source,
        )
        assert discarded.status == "completed", discarded.error
        assert discarded.output.writing_draft.status == "discarded"
        assert await h.runtime.store.head(h.story_id) == latest
    finally:
        await h.cleanup()


async def test_draft_feedback_can_request_npc_with_nested_human_approval() -> None:
    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    try:
        await h.create()
        initial = await h.runtime.store.head(h.story_id)
        pending = await h.execute(GenerateRequest(instruction="第一幕"))
        edited = await h.execute(
            ReviewDraftRequest(
                generation_command_id=pending.submission.id,
                expected_draft_version=1,
                decision="revise",
                feedback="加入一个新的持续角色 NPC",
            )
        )
        assert edited.status == "waiting" and edited.output.approval_phase == "consent", edited.error
        assert await h.runtime.store.head(h.story_id) == initial
        consent = await h.execute(
            ResolveCharacterRequest(generation_command_id=edited.submission.id, decision="consent")
        )
        assert consent.status == "completed", consent.error
        approval = await h.execute(
            ResolveCharacterRequest(
                generation_command_id=edited.submission.id,
                decision="accept",
                character=consent.output.character,
            )
        )
        assert approval.status == "completed", approval.error
        await h.worker.process(edited.submission.id)
        result = await h.runtime.store.command(edited.submission.id)
        assert result.status == "completed" and result.output.writing_draft.version == 2, result.error
        assert await h.runtime.store.head(h.story_id) == initial
        assert (await h.runtime.store.command(pending.submission.id)).output.writing_draft.version == 2
        confirmed = await h.confirm_generation(pending.submission.id)
        assert confirmed.status == "completed", confirmed.error
        assert len((await h.runtime.store.head(h.story_id)).story.characters) == 2
    finally:
        await h.cleanup()
