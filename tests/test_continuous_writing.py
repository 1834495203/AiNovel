"""Persistent author memory, evolving profiles, compact patches and native HITL recovery."""

import os
from uuid import uuid4

import pytest
from configured_fields import make_world
from sqlalchemy import delete, text
from test_integration import Harness

from ainovel.core.characters import Alias, ProfilePatch, Snapshot, StatePatch
from ainovel.core.domain import (
    CharacterEdit,
    CommandOutput,
    CommandSubmission,
    CreateStoryRequest,
    CreationProposal,
    GenerateRequest,
    InterviewRequest,
    RecoverCommandRequest,
    Story,
    Turn,
    UndoRequest,
    UpdateStoryRequest,
)
from ainovel.core.records import (
    CollectionPatch,
    CustomField,
    Entry,
    PreferencePatch,
    StoryMetadataPatch,
    WorldPatch,
    WriterPatch,
)
from ainovel.llm.mock import MockModel
from ainovel.mapper.models import CommandRow
from ainovel.service.undo import UndoService


def test_typed_collection_patch_keeps_unmodified_records_and_rejects_conflicts() -> None:
    world = CreationProposal(world=make_world("城市")).world
    rule = Entry(name="规则", value="魔法需要代价")
    world = WorldPatch(entries=CollectionPatch[Entry](upsert=[rule])).apply(world)
    changed = rule.model_copy(update={"description": "代价需要支付记忆", "reason": "用户修改"})
    world = WorldPatch(entries=CollectionPatch[Entry](upsert=[changed])).apply(world)
    assert world.premise == "城市"
    assert next(item for item in world.entries if item.id == rule.id) == changed
    with pytest.raises(ValueError):
        CollectionPatch[Entry](upsert=[changed], remove=[rule.id])
    assert (
        StatePatch(entries=CollectionPatch[Entry](upsert=[Entry(name="情绪", value="紧张")]))
        .apply(Snapshot(character_id=uuid4()).state)
        .entries[0]
        .value
        == "紧张"
    )


def test_old_story_schema_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Story.model_validate({"setup": {"title": "旧小说", "world": "都市"}})


integration = pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Local PostgreSQL/Redis")


@integration
async def test_writing_keeps_user_fields_empty_and_uses_only_one_agent() -> None:
    from unittest.mock import AsyncMock

    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        await h.execute(GenerateRequest(instruction="第一幕"))
        model = h.runtime.services.model
        model.choose_agent_tool = AsyncMock(side_effect=model.choose_agent_tool)
        second = await h.execute(GenerateRequest(instruction="继续"))
        assert second.status == "completed", second.error
        story = (await h.runtime.store.head(h.story_id)).story
        assert story.writer.entries == []
        writing_calls = [c for c in model.choose_agent_tool.call_args_list if c.args[0].task == "writing"]
        assert len(writing_calls) == 1
        assert {c.args[0].task for c in model.choose_agent_tool.call_args_list} <= {
            "scene_planning",
            "writing",
        }
    finally:
        await h.cleanup()


@integration
async def test_character_history_persists_deltas_alias_reasons_and_stage_profiles() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        await h.execute(GenerateRequest(instruction="第一幕"))
        first_story = (await h.runtime.store.head(h.story_id)).story
        character = first_story.characters[0]
        first_scene = first_story.scenes[0].id

        class AliasModel(MockModel):
            async def choose_agent_tool(self, context, command_id, *, prompt_template=None):
                from ainovel.core.agent import SubmitDraftAction

                action = await super().choose_agent_tool(context, command_id, prompt_template=prompt_template)
                if isinstance(action, SubmitDraftAction):
                    action.arguments.updates[0].aliases.upsert.append(
                        Alias(name="小林", reason="朋友在本幕使用的称呼")
                    )
                return action

        h.runtime.services.model = AliasModel(h.runtime.sink)
        h.runtime.services.writing_agent.model = h.runtime.services.model
        await h.execute(GenerateRequest(instruction="朋友用绰号小林称呼主角"))
        story = (await h.runtime.store.head(h.story_id)).story
        latest = story.snapshot(character.id)
        assert len(latest.experiences) == 2 and latest.aliases[0].reason
        assert not story.snapshot(character.id, first_scene).aliases
        assert all(len(scene.character_patches[0].new_experiences) == 1 for scene in story.scenes)
        assert all("experiences" not in scene.model_dump() for scene in story.scenes)
        addition = Entry(name="接受调查员训练", reason="第二幕之后用户补充")
        changed = await h.execute(
            UpdateStoryRequest(
                patch=StoryMetadataPatch(),
                character_edits=[
                    CharacterEdit(
                        character_id=character.id,
                        profile=ProfilePatch(entries=CollectionPatch[Entry](upsert=[addition])),
                        reason="用户扩充背景",
                    )
                ],
            )
        )
        assert changed.status == "completed", changed.error
        story = (await h.runtime.store.head(h.story_id)).story
        assert addition in story.snapshot(character.id).profile.entries
        assert addition not in story.snapshot(character.id, first_scene).profile.entries
    finally:
        await h.cleanup()


@integration
async def test_world_preferences_and_author_records_are_editable_and_atomically_undoable() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        await h.create()
        original = h.head
        rule = Entry(name="魔法代价", value="每次施法消耗记忆", reason="用户指定")
        field = CustomField(name="其他规则", children=[CustomField(name="冷却时间", value=10)])
        like = Entry(name="多视角", value=True, reason="用户修改偏好")
        thread = Entry(name="寻找失去的记忆", value="发现缺失记忆")
        result = await h.execute(
            UpdateStoryRequest(
                patch=StoryMetadataPatch(
                    title="新名称",
                    world=WorldPatch(
                        entries=CollectionPatch[Entry](upsert=[rule, field]),
                    ),
                    preferences=PreferencePatch(entries=CollectionPatch[Entry](upsert=[like])),
                    writer=WriterPatch(entries=CollectionPatch[Entry](upsert=[thread])),
                )
            )
        )
        assert result.status == "completed", result.error
        current = (await h.runtime.store.head(h.story_id)).story
        assert rule in current.world.entries
        assert next(item for item in current.world.entries if item.id == field.id).children[0].value == 10
        assert current.world.premise == "近未来都市"
        assert like in current.preferences.entries and current.writer.entries[0] == thread
        assert (
            next(item for item in await h.runtime.store.stories() if item.id == h.story_id).title == "新名称"
        )
        state = await UndoService(h.runtime.store).state(h.story_id)
        undone = await h.execute(UndoRequest(expected_operation_id=state.operation_id))
        assert undone.status == "completed", undone.error
        assert h.head == original
        assert (
            next(item for item in await h.runtime.store.stories() if item.id == h.story_id).title != "新名称"
        )
    finally:
        await h.cleanup()


@integration
async def test_undo_does_not_load_an_unrelated_story_checkpoint() -> None:
    """A malformed pending checkpoint in another story must not block this story's undo."""
    h = Harness()
    await h.runtime.store.initialize()
    unrelated_command_id = uuid4()
    try:
        await h.create()
        unrelated_story_id = uuid4()
        unrelated_submission = CommandSubmission(
            story_id=unrelated_story_id,
            base_revision_id=h.head,
            payload=GenerateRequest(instruction="synthetic pending command"),
        )
        async with h.runtime.store.sessions.begin() as session:
            session.add(
                CommandRow(
                    id=unrelated_command_id,
                    story_id=unrelated_story_id,
                    status="queued",
                    payload=unrelated_submission,
                    output=CommandOutput(),
                )
            )
            await session.flush()
            # Deliberately malformed typed state: the filtered undo query must never
            # deserialize this unrelated command's checkpoint.
            await session.execute(
                text("UPDATE commands SET checkpoint = '{}' WHERE id = :command_id"),
                {"command_id": unrelated_command_id},
            )

        undo_state = await UndoService(h.runtime.store).state(h.story_id)
        undone = await h.execute(UndoRequest(expected_operation_id=undo_state.operation_id))
        assert undone.status == "completed", undone.error
    finally:
        async with h.runtime.store.sessions.begin() as session:
            await session.execute(delete(CommandRow).where(CommandRow.id == unrelated_command_id))
        await h.cleanup()


@integration
async def test_invalid_checkpoint_fails_isolated_and_can_be_withdrawn_and_undone() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    bad_command_id = uuid4()
    good_command_id = uuid4()
    bad_session_command_id = uuid4()
    bad_session_id = uuid4()
    try:
        await h.create()
        h.ids.append(bad_command_id)
        submission = CommandSubmission(
            id=bad_command_id,
            story_id=h.story_id,
            base_revision_id=h.head,
            payload=GenerateRequest(instruction="synthetic bad checkpoint"),
        )
        async with h.runtime.store.sessions.begin() as session:
            session.add(
                CommandRow(
                    id=bad_command_id,
                    story_id=h.story_id,
                    status="queued",
                    payload=submission,
                    output=CommandOutput(),
                )
            )
            await session.flush()
            await session.execute(
                text("UPDATE commands SET checkpoint = '{}' WHERE id = :command_id"),
                {"command_id": bad_command_id},
            )

        # The public command view falls back to a typed projection without touching
        # the malformed checkpoint; worker processing records a durable failure.
        before = await h.runtime.store.command(bad_command_id)
        assert before.status == "queued" and before.error is None
        await h.worker.process(bad_command_id)
        failed = await h.runtime.store.command(bad_command_id)
        assert failed.status == "failed"
        assert "CheckpointValidationError" in failed.error and str(bad_command_id) in failed.error
        story_history = await h.runtime.store.commands(h.story_id)
        assert any(item.submission.id == bad_command_id and item.status == "failed" for item in story_history)

        h.ids.append(good_command_id)
        good_story_id = uuid4()
        good_submission = CommandSubmission(
            id=good_command_id,
            story_id=good_story_id,
            base_revision_id=h.head,
            payload=GenerateRequest(instruction="synthetic valid queued command"),
        )
        async with h.runtime.store.sessions.begin() as session:
            session.add(
                CommandRow(
                    id=good_command_id,
                    story_id=good_story_id,
                    status="queued",
                    payload=good_submission,
                    output=CommandOutput(),
                )
            )
        await h.queue.redis.lpush(h.queue.processing, str(bad_command_id))
        await h.worker.recover_once()
        assert await h.queue.redis.lrange(h.queue.processing, 0, -1) == []
        assert str(good_command_id) in await h.queue.redis.lrange(h.queue.waiting, 0, -1)

        h.ids.append(bad_session_command_id)
        session_submission = CommandSubmission(
            id=bad_session_command_id,
            session_id=bad_session_id,
            payload=InterviewRequest(turns=[Turn(role="user", content="synthetic session")]),
        )
        async with h.runtime.store.sessions.begin() as session:
            session.add(
                CommandRow(
                    id=bad_session_command_id,
                    story_id=None,
                    status="failed",
                    payload=session_submission,
                    output=CommandOutput(),
                    error="CheckpointValidationError: synthetic",
                )
            )
            await session.flush()
            await session.execute(
                text("UPDATE commands SET checkpoint = '{}' WHERE id = :command_id"),
                {"command_id": bad_session_command_id},
            )
        session_history = await h.runtime.store.session_commands(bad_session_id)
        assert any(
            item.submission.id == bad_session_command_id and item.status == "failed"
            for item in session_history
        )

        async with h.runtime.store.sessions() as session:
            raw_checkpoint = await session.scalar(
                text("SELECT checkpoint FROM commands WHERE id = :command_id"),
                {"command_id": bad_command_id},
            )
        assert raw_checkpoint == "{}"
        failure_event = (await h.runtime.store.events(bad_command_id))[-1]
        retry = await h.execute(
            RecoverCommandRequest(
                target_command_id=bad_command_id,
                expected_event_id=failure_event.id,
                decision="retry",
            )
        )
        assert retry.status == "failed" and "不能安全重试" in retry.error

        withdrawn = await h.execute(
            RecoverCommandRequest(
                target_command_id=bad_command_id,
                expected_event_id=failure_event.id,
                decision="withdraw",
            )
        )
        assert withdrawn.status == "completed", withdrawn.error
        undo_state = await UndoService(h.runtime.store).state(h.story_id)
        undone = await h.execute(UndoRequest(expected_operation_id=undo_state.operation_id))
        assert undone.status == "completed", undone.error

        recovered_source = await h.runtime.store.command(bad_command_id)
        assert recovered_source.status == "failed" and not recovered_source.undone
        async with h.runtime.store.sessions() as session:
            raw_checkpoint = await session.scalar(
                text("SELECT checkpoint FROM commands WHERE id = :command_id"),
                {"command_id": bad_command_id},
            )
        assert raw_checkpoint == "{}"
    finally:
        async with h.runtime.store.sessions.begin() as session:
            await session.execute(
                text("UPDATE commands SET checkpoint = NULL WHERE id = :command_id"),
                {"command_id": bad_command_id},
            )
            await session.execute(
                text("UPDATE commands SET checkpoint = NULL WHERE id = :command_id"),
                {"command_id": bad_session_command_id},
            )
        await h.cleanup()


@integration
async def test_creation_checklist_reports_known_missing_and_optional_fields() -> None:
    h = Harness()
    await h.runtime.store.initialize()
    try:
        first = await h.execute(InterviewRequest())
        assert len(first.output.interview.checklist) == 4
        assert (
            first.output.interview.checklist[0].required
            and first.output.interview.checklist[0].status == "missing"
        )
        next_round = await h.execute(InterviewRequest(turns=[Turn(role="user", content="近未来海上城市")]))
        result = next_round.output.interview
        assert result.proposal.world.entries[0].value == "近未来海上城市"
        assert result.checklist[0].status == "defined" and result.checklist[1].status == "missing"
        created = await h.execute(CreateStoryRequest(proposal=result.proposal, confirmed=True))
        assert created.status == "completed", created.error
    finally:
        await h.cleanup()


@integration
async def test_prose_and_material_changes_are_submitted_together_and_confirmed_atomically() -> None:
    from ainovel.core.agent import SubmitDraftAction
    from ainovel.core.domain import EditFieldsRequest
    from ainovel.core.fields import FieldTarget

    place = Entry(name="旧港", value="城里已经废弃的港口", reason="本幕正文建立的地点")

    class MaterialModel(MockModel):
        async def choose_agent_tool(self, context, command_id, *, prompt_template=None):
            action = await super().choose_agent_tool(context, command_id, prompt_template=prompt_template)
            if isinstance(action, SubmitDraftAction):
                action.arguments.materials.append(
                    EditFieldsRequest(
                        target=FieldTarget(scope="world"),
                        patch=CollectionPatch[Entry](upsert=[place]),
                        reason="正文带来的事实变化",
                    )
                )
            return action

    h = Harness(auto_confirm=False)
    await h.runtime.store.initialize()
    h.runtime.services.model = MaterialModel(h.runtime.sink)
    h.runtime.services.writing_agent.model = h.runtime.services.model
    try:
        await h.create()
        generated = await h.execute(GenerateRequest(instruction="平稳推进"))
        assert generated.status == "waiting", generated.error
        before = await h.runtime.store.head(h.story_id)
        assert place not in before.story.world.entries
        confirmed = await h.confirm_generation(generated.submission.id)
        assert confirmed.status == "completed", confirmed.error
        story = (await h.runtime.store.head(h.story_id)).story
        assert place in story.world.entries and len(story.scenes) == 1
        state = await UndoService(h.runtime.store).state(h.story_id)
        assert (await h.execute(UndoRequest(expected_operation_id=state.operation_id))).status == "completed"
        assert await h.runtime.store.head(h.story_id) == before
    finally:
        await h.cleanup()
