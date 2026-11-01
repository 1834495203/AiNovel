"""Custom definitions, legacy migration and branch-safe event recovery on disposable SQLite."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from ainovel.core.config import Settings
from ainovel.core.domain import (
    Chapter,
    CommandSubmission,
    CreateStoryRequest,
    CreationProposal,
    EditFieldsRequest,
    Revision,
    Story,
)
from ainovel.core.field_history import apply_patches, diff_values
from ainovel.core.field_templates import default_templates, instantiate, merge_template
from ainovel.core.fields import (
    ApplyFieldTemplateRequest,
    FieldDefinition,
    FieldTarget,
    FieldTemplate,
    SaveFieldTemplateRequest,
)
from ainovel.core.records import (
    CollectionPatch,
    Entry,
    PlotThread,
    Preference,
    ReaderPreferences,
    WorldSetting,
    WriterWorkspace,
)
from ainovel.mapper.field_history import append, restore
from ainovel.mapper.field_templates import FieldTemplateRepository
from ainovel.mapper.models import CommandRow, FieldChangeRow, FieldSnapshotRow, RevisionRow, StoryRow
from ainovel.mapper.store import Store
from ainovel.service.fields import (
    FIELD_TEMPLATE_SCOPE_ID,
    ApplyFieldTemplateHandler,
    EditFieldsHandler,
    SaveFieldTemplateHandler,
    read_fields,
)


def test_templates_are_definitions_not_values_and_instances_are_independent():
    definition = FieldDefinition(
        name="装备",
        type="list",
        item=FieldDefinition(
            name="装备",
            type="object",
            children=[
                FieldDefinition(name="名称", required=True),
                FieldDefinition(name="重量", type="number"),
            ],
        ),
    )
    template = FieldTemplate(name="角色装备", scope="character", fields=[definition])
    first = merge_template([], template, uuid4())
    second = merge_template([], template, uuid4())
    assert first[0].id != second[0].id
    assert first[0].definition.id == second[0].definition.id == definition.id
    item = instantiate(definition.item)
    item.children[0].value = "长剑"
    item.children[1].value = 3
    first[0].children = [item]
    assert not second[0].children
    assert merge_template(first, template, uuid4()) == first
    with pytest.raises(ValidationError):
        Entry(name="重量", definition=FieldDefinition(name="重量", type="number"), value=True)
    with pytest.raises(ValidationError):
        FieldDefinition(name="立场", type="enum", options=[])


def test_fixed_material_groups_are_canonical_custom_entries_and_old_data_round_trips():
    line = PlotThread(name="逃离港口", next_steps=["找到船"])
    writer = WriterWorkspace(
        mainlines=[line], progress="第一幕", custom_fields=[Entry(name="额外笔记", value="留意天气")]
    )
    preferences = ReaderPreferences(likes=[Preference(name="节奏紧凑")])
    world = WorldSetting(premise="浮空城")
    assert (
        set(WriterWorkspace.model_fields)
        == set(ReaderPreferences.model_fields)
        == set(WorldSetting.model_fields)
        == {"entries"}
    )
    assert set(writer.model_dump(round_trip=True)) == {"entries"}
    assert WriterWorkspace.model_validate_json(writer.model_dump_json(round_trip=True)).mainlines == [line]
    assert ReaderPreferences.model_validate_json(preferences.model_dump_json()).likes == preferences.likes
    assert WorldSetting.model_validate_json(world.model_dump_json()).premise == "浮空城"
    assert any(entry.definition and entry.definition.name == "主线" for entry in writer.entries)
    assert any(
        template.scope == "writer" and any(field.name == "创作笔记" for field in template.fields)
        for template in default_templates()
    )


def test_patch_addresses_use_ids_and_preserve_reordering_deletion_and_false_values():
    a, b = str(uuid4()), str(uuid4())
    before = {"entries": [{"id": a, "name": "旧名", "value": True}, {"id": b, "value": 5}]}
    after = {"entries": [{"id": b, "value": 0}, {"id": a, "name": "新名", "value": False}]}
    patches = diff_values(before, after)
    assert any(part.kind == "item" and part.value == a for patch in patches for part in patch.path)
    assert apply_patches(before, patches) == after
    deleted = {"entries": [after["entries"][0]]}
    assert apply_patches(after, diff_values(after, deleted)) == deleted
    assert before["entries"][0]["name"] == "旧名"
    # Python considers True == 1; event replay must still retain the type change.
    typed = {"value": True}
    patches = diff_values(typed, {"value": 1})
    assert patches and type(apply_patches(typed, patches)["value"]) is int


def test_custom_definitions_and_values_reach_the_writing_context():
    from ainovel.core.domain import Draft, WorkflowState
    from ainovel.llm.schemas import project_context

    chapter = Chapter(title="第一章", custom_fields=[Entry(name="主题", value="离别")])
    story = Story(
        title="自定义上下文", chapters=[chapter], custom_fields=[Entry(name="叙事人称", value="第二人称")]
    )
    story.preferences.custom_fields = [
        Entry(
            name="标点习惯",
            definition=FieldDefinition(name="标点习惯", description="避免感叹号"),
            value="克制",
        )
    ]
    result = project_context(Draft, WorkflowState(command_id=uuid4(), story=story, instruction="继续"))
    text = result.model_dump_json()
    assert all(value in text for value in ("第二人称", "离别", "避免感叹号", "克制"))
    default = next(template for template in default_templates() if template.scope == "writer").fields[0]
    changed = default.model_copy(update={"type": "text", "item": None})
    story.writer.entries = [Entry(name=changed.name, definition=changed, value="用自由文本记录主线")]
    result = project_context(Draft, WorkflowState(command_id=uuid4(), story=story, instruction="继续"))
    assert "用自由文本记录主线" in result.model_dump_json()


def test_snapshots_replay_and_keep_all_patches_across_sibling_branches(monkeypatch):
    import ainovel.mapper.field_history as journal

    monkeypatch.setattr(journal, "SNAPSHOT_INTERVAL", 3)

    async def run():
        store = Store(Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:"))
        await store.initialize()
        story = Story(title="仅测试用小说", custom_fields=[Entry(name="线索", value="原始")])
        revisions = []
        try:
            for index in range(8):
                story.custom_fields[0].value = index
                revision = Revision(
                    story_id=story.id,
                    parent_id=revisions[-1].id if revisions else None,
                    command_id=uuid4(),
                    reason=f"修改 {index}",
                    story=story.model_copy(deep=True),
                )
                async with store.sessions.begin() as session:
                    await append(session, revision)
                    session.add(RevisionRow(id=revision.id, story_id=story.id, payload=revision))
                revisions.append(revision)
            async with store.sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(FieldChangeRow)) == 8
                assert await session.scalar(select(func.count()).select_from(FieldSnapshotRow)) == 3
                assert (await restore(session, revisions[-1].id, story.id)).custom_fields[0].value == 7
                old = await restore(session, revisions[2].id, story.id)
                old.custom_fields[0].value = "分支 B"
                branch = Revision(
                    story_id=story.id, parent_id=revisions[2].id, command_id=uuid4(), reason="分支", story=old
                )
                await append(session, branch)
                session.add(RevisionRow(id=branch.id, story_id=story.id, payload=branch))
            async with store.sessions() as session:
                assert (await restore(session, branch.id, story.id)).custom_fields[0].value == "分支 B"
                assert (await restore(session, revisions[-1].id, story.id)).custom_fields[0].value == 7
                assert (await restore(session, revisions[2].id, story.id)).custom_fields[0].value == 2
                assert await session.scalar(select(func.count()).select_from(FieldChangeRow)) == 9
        finally:
            await store.close()

    asyncio.run(run())


def test_legacy_revision_is_a_baseline_and_new_reads_replay_the_journal():
    async def run():
        store = Store(Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:"))
        await store.initialize()
        story = Story(title="旧小说", world="旧世界")
        old = Revision(story_id=story.id, command_id=uuid4(), reason="旧版本", story=story)
        try:
            async with store.sessions.begin() as session:
                session.add(RevisionRow(id=old.id, story_id=story.id, payload=old))
                session.add(StoryRow(id=story.id, head_id=old.id, title=story.title))
            changed = story.model_copy(deep=True)
            changed.world.premise = "新世界"
            new = Revision(
                story_id=story.id, parent_id=old.id, command_id=uuid4(), reason="新事件", story=changed
            )
            async with store.sessions.begin() as session:
                await append(session, new)
                session.add(RevisionRow(id=new.id, story_id=story.id, payload=new))
                row = await session.get(StoryRow, story.id)
                row.head_id = new.id
            assert (await store.head(story.id)).story.world.premise == "新世界"
            assert (await store.revision(old.id)).story.world.premise == "旧世界"
        finally:
            await store.close()

    asyncio.run(run())


def test_template_save_batch_apply_edits_and_undo_are_atomic_and_persistent():
    from ainovel.core.domain import UndoRequest
    from ainovel.service.undo import UndoService

    class Services:
        def __init__(self, store):
            self.store = store

        async def source(self, submission):
            return (await self.store.revision(submission.base_revision_id)).story.model_copy(deep=True)

        async def commit(self, submission, story, reason):
            return await self.store.commit(submission, story, reason, [], [])

    async def run():
        store = Store(Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:"))
        await store.initialize()
        services = Services(store)
        try:
            story = Story(title="模板测试", chapters=[Chapter(title="一"), Chapter(title="二")])
            create = CommandSubmission(
                payload=CreateStoryRequest(
                    proposal=CreationProposal(title=story.title, world="城"), confirmed=True
                )
            )

            async def start(submission):
                await store.submit(submission)
                async with store.sessions.begin() as session:
                    row = await session.get(CommandRow, submission.id)
                    row.status = "running"

            await start(create)
            await store.commit(create, story, "创建", [], [])
            template = FieldTemplate(
                name="章节冲突", scope="chapter", fields=[FieldDefinition(name="主要冲突")]
            )
            save = CommandSubmission(
                session_id=FIELD_TEMPLATE_SCOPE_ID, payload=SaveFieldTemplateRequest(template=template)
            )
            await start(save)
            await SaveFieldTemplateHandler(services).handle(save)
            assert template in await FieldTemplateRepository(store).list()
            view = await store.head(story.id)
            targets = [FieldTarget(scope="chapter", entity_id=item.id) for item in story.chapters]
            apply = CommandSubmission(
                story_id=story.id,
                base_revision_id=view.revision_id,
                payload=ApplyFieldTemplateRequest(template_id=template.id, targets=targets),
            )
            await start(apply)
            await ApplyFieldTemplateHandler(services).handle(apply)
            view = await store.head(story.id)
            assert all(len(item.custom_fields) == 1 for item in view.story.chapters)
            field = read_fields(view.story, targets[0])[0].model_copy(update={"value": "暴雨"})
            edit = CommandSubmission(
                story_id=story.id,
                base_revision_id=view.revision_id,
                payload=EditFieldsRequest(target=targets[0], patch=CollectionPatch[Entry](upsert=[field])),
            )
            await start(edit)
            await EditFieldsHandler(services).handle(edit)
            assert (await store.head(story.id)).story.chapters[0].custom_fields[0].value == "暴雨"
            # Saved template is immutable and contains no filled instance value.
            assert (await FieldTemplateRepository(store).get(template.id)) == template
            state = await UndoService(store).state(FIELD_TEMPLATE_SCOPE_ID)
            undo = CommandSubmission(
                session_id=FIELD_TEMPLATE_SCOPE_ID,
                payload=UndoRequest(expected_operation_id=state.operation_id),
            )
            await start(undo)
            await UndoService(store).execute(undo, undo.payload)
            assert template not in await FieldTemplateRepository(store).list()
            # Applying a template pinned its definitions; hiding the template cannot erase them.
            assert (await store.head(story.id)).story.chapters[0].custom_fields[
                0
            ].definition == template.fields[0]
        finally:
            await store.close()

    asyncio.run(run())
