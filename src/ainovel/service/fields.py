"""Generic field commands shared by templates, author edits and future agent tools."""

from uuid import NAMESPACE_URL, uuid5

from ainovel.core.characters import CharacterPatch, ProfilePatch, StatePatch
from ainovel.core.domain import CommandOutput, CommandSubmission, EditFieldsRequest, Story
from ainovel.core.errors import ConflictError
from ainovel.core.field_templates import copy_template_set, merge_template
from ainovel.core.fields import (
    ApplyFieldTemplateRequest,
    FieldTarget,
    FieldTemplateSet,
    InitializeStoryTemplateSetRequest,
    SaveFieldTemplateRequest,
    SaveFieldTemplateSetRequest,
    UpdateStoryFieldSchemaRequest,
)
from ainovel.core.records import CollectionPatch, Entry, merge_records
from ainovel.core.story_fields import read_fields, story_schema, targets_for
from ainovel.mapper.field_templates import FieldTemplateRepository
from ainovel.service.commands import Handler

FIELD_TEMPLATE_SCOPE_ID = uuid5(NAMESPACE_URL, "ainovel:field-template-library")


def field_target_locked(story: Story, target: FieldTarget) -> bool:
    return any(scene.text.strip() and (
        target.scope == "scene" and scene.id == target.entity_id
        or target.scope == "chapter" and scene.chapter_id == target.entity_id
    ) for scene in story.scenes)


def write_fields(story: Story, target: FieldTarget, entries: list[Entry], reason: str) -> None:
    if field_target_locked(story, target) and entries != read_fields(story, target):
        raise ConflictError("本幕已有正文，原章节或幕的设定已锁定，只能重新生成文本")
    # Validate the complete candidate, including nested values, before mutating business state.
    entries = [Entry.model_validate(entry.model_dump(round_trip=True)) for entry in entries]
    if len({entry.id for entry in entries}) != len(entries):
        raise ValueError("顶层字段 ID 不可重复")
    definitions = [entry.definition.id for entry in entries if entry.definition]
    if len(definitions) != len(set(definitions)):
        raise ValueError("同一资料中不能重复填写同一顶层字段定义")
    if target.scope == "story":
        story.custom_fields = entries
    elif target.scope in ("world", "preferences", "writer"):
        getattr(story, target.scope).entries = entries
    elif target.scope in ("chapter", "scene"):
        entities = story.chapters if target.scope == "chapter" else story.scenes
        entity = next((item for item in entities if item.id == target.entity_id), None)
        if entity is None:
            raise ValueError("字段目标不存在")
        entity.custom_fields = entries
    else:
        old = read_fields(story, target)
        patch = CollectionPatch[Entry](
            upsert=entries, remove=[item.id for item in old if item.id not in {entry.id for entry in entries}]
        )
        if story.scenes:
            story.scenes[-1].character_patches.append(
                CharacterPatch(
                    character_id=target.entity_id,
                    scene_id=story.scenes[-1].id,
                    reason=reason,
                    profile=ProfilePatch(entries=patch) if target.scope == "character" else ProfilePatch(),
                    state=StatePatch(entries=patch) if target.scope == "state" else StatePatch(),
                )
            )
        else:
            baseline = next(item for item in story.initial_snapshots if item.character_id == target.entity_id)
            if target.scope == "character":
                baseline.profile.entries = entries
                character = next(item for item in story.characters if item.id == target.entity_id)
                character.profile.entries = [item.model_copy(deep=True) for item in entries]
            else:
                baseline.state.entries = entries


class EditFieldsHandler(Handler[EditFieldsRequest]):
    request_type = EditFieldsRequest

    async def execute(self, submission: CommandSubmission, request: EditFieldsRequest) -> CommandOutput:
        story = await self.services.source(submission)
        write_fields(
            story,
            request.target,
            merge_records(read_fields(story, request.target), request.patch),
            request.reason,
        )
        return await self.services.commit(submission, story, request.reason)


class ApplyFieldTemplateHandler(Handler[ApplyFieldTemplateRequest]):
    request_type = ApplyFieldTemplateRequest

    async def execute(
        self, submission: CommandSubmission, request: ApplyFieldTemplateRequest
    ) -> CommandOutput:
        template = await FieldTemplateRepository(self.services.store).get(request.template_id)
        story = await self.services.source(submission)
        targets = [(target.scope, target.entity_id) for target in request.targets]
        if len(targets) != len(set(targets)):
            raise ValueError("不能重复应用到同一目标")
        for target in request.targets:
            if template.scope != target.scope:
                raise ValueError("模板适用范围与目标不一致")
            namespace = target.entity_id or uuid5(story.id, target.scope)
            entries = merge_template(read_fields(story, target), template, namespace)
            write_fields(story, target, entries, f"应用字段模板：{template.name}")
        return await self.services.commit(submission, story, f"应用字段模板：{template.name}")


class SaveFieldTemplateHandler(Handler[SaveFieldTemplateRequest]):
    request_type = SaveFieldTemplateRequest

    async def execute(
        self, submission: CommandSubmission, request: SaveFieldTemplateRequest
    ) -> CommandOutput:
        if submission.session_id != FIELD_TEMPLATE_SCOPE_ID:
            raise ValueError("字段模板需要固定的模板库撤回范围")
        return await FieldTemplateRepository(self.services.store).save(submission, request.template)


def initialize_story_schema(story: Story, template_set: FieldTemplateSet) -> None:
    story.field_template_set = copy_template_set(template_set)
    for template in story.field_template_set.templates:
        for target in targets_for(story, template.scope):
            if field_target_locked(story, target):
                continue
            namespace = target.entity_id or uuid5(story.id, target.scope)
            write_fields(story, target, merge_template(read_fields(story, target), template, namespace), "初始化本书字段结构")


class InitializeStoryTemplateSetHandler(Handler[InitializeStoryTemplateSetRequest]):
    request_type = InitializeStoryTemplateSetRequest

    async def execute(self, submission: CommandSubmission, request: InitializeStoryTemplateSetRequest) -> CommandOutput:
        source = await FieldTemplateRepository(self.services.store).get_set(request.template_set_id)
        story = await self.services.source(submission)
        initialize_story_schema(story, source)
        return await self.services.commit(submission, story, f"为本书独立初始化模板套装：{source.name}")


class UpdateStoryFieldSchemaHandler(Handler[UpdateStoryFieldSchemaRequest]):
    request_type = UpdateStoryFieldSchemaRequest

    async def execute(self, submission: CommandSubmission, request: UpdateStoryFieldSchemaRequest) -> CommandOutput:
        story = await self.services.source(submission)
        local = story_schema(story)
        changes = {item.scope: item.model_copy(deep=True) for item in request.templates}
        local.templates = [changes.get(item.scope, item) for item in local.templates]
        story.field_template_set = local
        return await self.services.commit(submission, story, "修改本书新增对象的初始化字段，原模板不变")


class SaveFieldTemplateSetHandler(Handler[SaveFieldTemplateSetRequest]):
    request_type = SaveFieldTemplateSetRequest

    async def execute(self, submission: CommandSubmission, request: SaveFieldTemplateSetRequest) -> CommandOutput:
        if submission.session_id != FIELD_TEMPLATE_SCOPE_ID:
            raise ValueError("小说模板套装需要独立的模板库操作范围")
        return await FieldTemplateRepository(self.services.store).save_set(submission, request.template_set, request.replace_template_set_id)
