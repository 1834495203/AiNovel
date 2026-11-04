from uuid import uuid4

import pytest
from pydantic import ValidationError

from ainovel.core.characters import CharacterSpec, Profile, Snapshot
from ainovel.core.domain import (
    CommandSubmission,
    CreateStoryRequest,
    CreationProposal,
    Draft,
    Extraction,
    Plan,
    Story,
    WritingState,
)
from ainovel.core.field_templates import default_template_set
from ainovel.core.records import CollectionPatch, PlotThread, WriterPatch
from ainovel.service.commands import Services


def test_default_templates_and_new_entities_have_no_implicit_fields():
    assert all(not template.fields for template in default_template_set().templates)
    character=CharacterSpec(name="甲")
    story=Story(title="空结构")
    assert character.profile.entries == []
    assert story.world.entries == story.preferences.entries == story.writer.entries == []
    state=WritingState(command_id=uuid4(),story=story,instruction="按计划写",plan=Plan(chapter_plan="一章",scene_plan="来信",character_names=[]),draft=Draft(title="来信",text="正文",summary="摘要"),extraction=Extraction(updates=[]))
    saved=Services.apply_scene(state)
    assert saved.writer.entries == []
    assert saved.chapters[0].custom_fields == saved.scenes[0].custom_fields == []


def test_missing_fields_are_not_recreated_by_typed_writes():
    story=Story(title="空结构")
    patched=WriterPatch(mainlines=CollectionPatch[PlotThread](upsert=[PlotThread(name="不应自动创建")]),progress="不应自动创建").apply(story.writer)
    assert patched.entries == []
    assert set(patched.model_dump()) == {"entries"}


def test_removed_old_formats_are_rejected():
    with pytest.raises(ValidationError):
        Profile(background="旧字段")
    with pytest.raises(ValidationError):
        CreationProposal(world="旧世界字符串")
    with pytest.raises(ValidationError):
        CreateStoryRequest(setup=CreationProposal(title="旧参数"),confirmed=True)
    assert Snapshot(character_id=uuid4()).profile.entries == []
    assert CommandSubmission(payload=CreateStoryRequest(proposal=CreationProposal(title="新参数"),confirmed=True))
