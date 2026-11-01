"""Novel initialization copies, future entities, and value-free exports."""

import os
from uuid import uuid4

import pytest

from ainovel.core.characters import CharacterSpec
from ainovel.core.config import Settings
from ainovel.core.domain import (
    CommandSubmission,
    CreateStoryRequest,
    CreationProposal,
    Draft,
    EditFieldsRequest,
    Extraction,
    Plan,
    UndoRequest,
    WorkflowState,
)
from ainovel.core.errors import ConflictError
from ainovel.core.field_templates import default_template_set, instantiate
from ainovel.core.fields import (
    FieldDefinition,
    FieldTarget,
    InitializeStoryTemplateSetRequest,
    SaveFieldTemplateSetRequest,
    UpdateStoryFieldSchemaRequest,
)
from ainovel.core.records import CollectionPatch
from ainovel.core.story_fields import export_story_schema, read_fields
from ainovel.mapper.field_templates import FieldTemplateRepository
from ainovel.mapper.models import CommandRow
from ainovel.mapper.store import Store
from ainovel.service.commands import CreateStoryHandler, Services
from ainovel.service.fields import (
    FIELD_TEMPLATE_SCOPE_ID,
    EditFieldsHandler,
    InitializeStoryTemplateSetHandler,
    SaveFieldTemplateSetHandler,
    UpdateStoryFieldSchemaHandler,
)
from ainovel.service.undo import UndoService


class Harness:
    def __init__(self, store):
        self.store = store

    async def source(self, submission):
        return (await self.store.revision(submission.base_revision_id)).story.model_copy(deep=True)

    async def commit(self, submission, story, reason):
        return await self.store.commit(submission, story, reason, [], [])

    async def execute(self, handler, payload, story_id=None):
        head = await self.store.head(story_id) if story_id else None
        submission = CommandSubmission(payload=payload, story_id=story_id,
            base_revision_id=head.revision_id if head else None,
            session_id=FIELD_TEMPLATE_SCOPE_ID if isinstance(payload, SaveFieldTemplateSetRequest) else None)
        await self.store.submit(submission)
        async with self.store.sessions.begin() as session:
            row = await session.get(CommandRow, submission.id)
            row.status = "running"
        return await handler(self).handle(submission)

    async def save_set(self, value):
        return await self.execute(SaveFieldTemplateSetHandler, SaveFieldTemplateSetRequest(template_set=value))

    async def create(self, template_set_id=None):
        output = await self.execute(CreateStoryHandler, CreateStoryRequest(
            confirmed=True, field_template_set_id=template_set_id,
            proposal=CreationProposal(title="独立小说", world="合成世界", characters=[CharacterSpec(name="甲")]),
        ))
        return (await self.store.head(output.story_id)).story


@pytest.fixture
async def h():
    store = Store(Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:"))
    await store.initialize()
    try:
        yield Harness(store)
    finally:
        await store.close()


def example_set():
    result = default_template_set().model_copy(deep=True)
    result.id, result.builtin, result.name = uuid4(), False, "修仙模板"
    additions = {
        "world": FieldDefinition(name="法则", description="填写世界运行规则"),
        "character": FieldDefinition(name="境界", type="enum", options=["凡人", "筑基"]),
        "state": FieldDefinition(name="受伤", type="boolean"),
        "chapter": FieldDefinition(name="本章目标"),
        "scene": FieldDefinition(name="发生地点"),
    }
    for item in result.templates:
        if item.scope in additions:
            item.fields.append(additions[item.scope])
    return result


def next_scene(story):
    return Services.apply_scene(WorkflowState(
        command_id=uuid4(), story=story, instruction="继续", draft=Draft(title="一幕", text="合成正文", summary="摘要"),
        extraction=Extraction(updates=[]), plan=Plan(chapter_plan="一章", scene_plan="一幕", character_names=[]),
    ))


async def test_each_novel_owns_a_full_independent_copy_and_initializes_new_entities(h):
    source = example_set()
    await h.save_set(source)
    a, b = await h.create(source.id), await h.create(source.id)
    assert a.field_template_set.id != b.field_template_set.id != source.id
    assert len(a.field_template_set.templates) == 8
    character = a.characters[0]
    assert "境界" in {entry.name for entry in a.snapshot(character.id).profile.entries}
    assert "受伤" in {entry.name for entry in a.snapshot(character.id).state.entries}
    written = next_scene(a)
    assert [entry.name for entry in written.chapters[0].custom_fields] == ["本章目标"]
    assert [entry.name for entry in written.scenes[0].custom_fields] == ["发生地点"]
    written.chapters[0].custom_fields[0].value = "只有甲书的目标"
    assert next_scene(b).chapters[0].custom_fields[0].value is None
    assert (await FieldTemplateRepository(h.store).get_set(source.id)) == source


async def test_field_edits_deletions_and_new_local_defaults_never_change_source(h):
    source = example_set()
    await h.save_set(source)
    story = await h.create(source.id)
    target = FieldTarget(scope="world")
    removed = next(entry for entry in read_fields(story, target) if entry.name == "法则")
    await h.execute(EditFieldsHandler, EditFieldsRequest(target=target,
        patch=CollectionPatch(remove=[removed.id]), reason="删除本书字段"), story.id)
    story = (await h.store.head(story.id)).story
    chapter = next(item for item in story.field_template_set.templates if item.scope == "chapter")
    chapter.fields = [FieldDefinition(name="本章谜题")]
    await h.execute(UpdateStoryFieldSchemaHandler, UpdateStoryFieldSchemaRequest(templates=[chapter]), story.id)
    changed = (await h.store.head(story.id)).story
    assert removed.id not in {entry.id for entry in changed.world.entries}
    written = next_scene(changed)
    assert removed.id not in {entry.id for entry in written.world.entries}, "后续写作不重新添加已删除的字段"
    assert [entry.name for entry in written.chapters[0].custom_fields] == ["本章谜题"]
    assert (await FieldTemplateRepository(h.store).get_set(source.id)) == source
    # The local schema participates in revision history and sequential undo.
    state = await UndoService(h.store).state(story.id)
    submission = CommandSubmission(story_id=story.id, base_revision_id=(await h.store.head(story.id)).revision_id,
                                   payload=UndoRequest(expected_operation_id=state.operation_id))
    await h.store.submit(submission)
    async with h.store.sessions.begin() as session:
        row = await session.get(CommandRow, submission.id)
        row.status = "running"
    await UndoService(h.store).execute(submission, submission.payload)
    restored = (await h.store.head(story.id)).story
    assert [entry.name for entry in next_scene(restored).chapters[0].custom_fields] == ["本章目标"]


async def test_export_uses_current_nested_structure_without_values_or_deleted_fields(h):
    source = example_set()
    await h.save_set(source)
    story = next_scene(await h.create(source.id))
    gear = FieldDefinition(name="法宝", type="object", children=[FieldDefinition(name="品阶"), FieldDefinition(name="秘密")])
    entry = instantiate(gear)
    entry.children[0].value = "不应导出的填写内容"
    entry.children.pop()  # Actual child deletion must not reappear from the old definition.
    entry.reason, entry.description = "不应导出的原因", "不应导出的笔记"
    character = story.characters[0]
    story.initial_snapshots[0].profile.entries.append(entry)
    character.profile.entries.append(entry.model_copy(deep=True))
    story.scenes[-1].custom_fields[0].name = "本幕视角"
    exported = export_story_schema(story, "调整后的新模板", "可复用说明", [
        FieldTarget(scope="character", entity_id=character.id),
        FieldTarget(scope="scene", entity_id=story.scenes[-1].id),
    ])
    assert not any(token in exported.model_dump_json() for token in ("不应导出", "合成正文", "秘密"))
    profile = next(item for item in exported.templates if item.scope == "character")
    assert next(item for item in profile.fields if item.name == "法宝").children[0].name == "品阶"
    assert next(item for item in exported.templates if item.scope == "scene").fields[0].name == "本幕视角"
    await h.save_set(exported)
    created = await h.create(exported.id)
    fresh = next_scene(created)
    assert fresh.scenes[0].custom_fields[0].name == "本幕视角"
    assert (await FieldTemplateRepository(h.store).get_set(source.id)) == source


async def test_library_undo_does_not_break_novel_copies_and_existing_novel_can_initialize(h):
    source = example_set()
    await h.save_set(source)
    story = await h.create()
    premise = story.world.premise
    await h.execute(InitializeStoryTemplateSetHandler, InitializeStoryTemplateSetRequest(template_set_id=source.id), story.id)
    changed = (await h.store.head(story.id)).story
    assert changed.world.premise == premise
    state = await UndoService(h.store).state(FIELD_TEMPLATE_SCOPE_ID)
    submission = CommandSubmission(session_id=FIELD_TEMPLATE_SCOPE_ID, payload=UndoRequest(expected_operation_id=state.operation_id))
    await h.store.submit(submission)
    async with h.store.sessions.begin() as session:
        row = await session.get(CommandRow, submission.id)
        row.status = "running"
    await UndoService(h.store).execute(submission, submission.payload)
    with pytest.raises(ValueError):
        await FieldTemplateRepository(h.store).get_set(source.id)
    assert next_scene(changed).scenes[0].custom_fields[0].name == "发生地点"
    with pytest.raises(ValueError):
        await h.save_set(source.model_copy(update={"builtin": True}))


async def test_direct_edit_keeps_one_visible_template_and_existing_novels_independent(h):
    source = example_set()
    await h.save_set(source)
    old_story = await h.create(source.id)
    edited = source.model_copy(deep=True)
    edited.id = uuid4()
    edited.name = "直接修改后的模板"
    next(item for item in edited.templates if item.scope == "scene").fields[0].name = "本幕视角"
    result = await h.execute(SaveFieldTemplateSetHandler, SaveFieldTemplateSetRequest(
        template_set=edited, replace_template_set_id=source.id,
    ))
    repository = FieldTemplateRepository(h.store)
    assert result.field_template_set.replaces_id == source.id
    assert [item.id for item in await repository.list_sets()] == [default_template_set().id, edited.id]
    assert (await repository.get_set(source.id)).id == edited.id
    assert next_scene(old_story).scenes[0].custom_fields[0].name == "发生地点"
    assert next_scene(await h.create(source.id)).scenes[0].custom_fields[0].name == "本幕视角"
    # Old editor state cannot overwrite an intervening edit.
    stale = source.model_copy(update={"id": uuid4(), "name": "过期修改"})
    with pytest.raises(ConflictError, match="其他操作修改"):
        await h.execute(SaveFieldTemplateSetHandler, SaveFieldTemplateSetRequest(
            template_set=stale, replace_template_set_id=source.id,
        ))
    assert (await repository.get_set(source.id)).name == edited.name


async def test_default_template_can_be_edited_directly_and_undone(h):
    source = default_template_set()
    edited = source.model_copy(deep=True)
    edited.id, edited.builtin, edited.name = uuid4(), False, "我的默认模板"
    next(item for item in edited.templates if item.scope == "chapter").fields = [FieldDefinition(name="章节目标")]
    await h.execute(SaveFieldTemplateSetHandler, SaveFieldTemplateSetRequest(
        template_set=edited, replace_template_set_id=source.id,
    ))
    repository = FieldTemplateRepository(h.store)
    assert [item.id for item in await repository.list_sets()] == [edited.id]
    story = await h.create()
    assert next_scene(story).chapters[0].custom_fields[0].name == "章节目标"
    state = await UndoService(h.store).state(FIELD_TEMPLATE_SCOPE_ID)
    submission = CommandSubmission(session_id=FIELD_TEMPLATE_SCOPE_ID, payload=UndoRequest(expected_operation_id=state.operation_id))
    await h.store.submit(submission)
    async with h.store.sessions.begin() as session:
        row = await session.get(CommandRow, submission.id)
        row.status = "running"
    await UndoService(h.store).execute(submission, submission.payload)
    assert (await repository.get_set(source.id)) == source
    assert next_scene(story).chapters[0].custom_fields[0].name == "章节目标"


@pytest.mark.skipif(os.getenv("AINOVEL_INTEGRATION") != "1", reason="Opt-in PostgreSQL mock workflow")
async def test_real_writing_graph_initializes_npc_chapter_and_scene_from_novel_copy():
    from test_integration import Harness as GraphHarness

    from ainovel.core.domain import GenerateRequest, ResolveCharacterRequest

    h = GraphHarness(auto_confirm=False)
    await h.runtime.store.initialize()
    source = example_set()
    try:
        save = CommandSubmission(session_id=FIELD_TEMPLATE_SCOPE_ID, payload=SaveFieldTemplateSetRequest(template_set=source))
        h.ids.append(save.id)
        await h.runtime.store.submit(save)
        await h.worker.process(save.id)
        assert (await h.runtime.store.command(save.id)).status == "completed"
        created = await h.execute(CreateStoryRequest(confirmed=True, field_template_set_id=source.id,
            proposal=CreationProposal(title="模板图验证", world="都市", characters=[CharacterSpec(name="甲")])) )
        assert created.status == "completed", created.error
        initial = await h.runtime.store.head(h.story_id)
        pending = await h.execute(GenerateRequest(instruction="引入一个新的持续角色 NPC"))
        assert pending.status == "waiting" and pending.output.approval_phase == "consent", pending.error
        consent = await h.execute(ResolveCharacterRequest(generation_command_id=pending.submission.id, decision="consent"))
        accepted = await h.execute(ResolveCharacterRequest(generation_command_id=pending.submission.id,
            decision="accept", character=consent.output.character))
        assert accepted.status == "completed", accepted.error
        await h.worker.process(pending.submission.id)
        waiting = await h.runtime.store.command(pending.submission.id)
        assert waiting.output.writing_draft is not None, waiting.error
        assert await h.runtime.store.head(h.story_id) == initial
        confirmed = await h.confirm_generation(pending.submission.id)
        assert confirmed.status == "completed", confirmed.error
        story = (await h.runtime.store.head(h.story_id)).story
        assert len(story.characters) == 2
        npc = story.characters[-1]
        assert "境界" in {entry.name for entry in story.snapshot(npc.id).profile.entries}
        assert "受伤" in {entry.name for entry in story.snapshot(npc.id).state.entries}
        assert story.chapters[0].custom_fields[0].name == "本章目标"
        assert story.scenes[0].custom_fields[0].name == "发生地点"
        assert (await FieldTemplateRepository(h.runtime.store).get_set(source.id)) == source
    finally:
        await h.cleanup()
