"""Novel-owned initialization structures, independent of the reusable library."""

from uuid import uuid5

from ainovel.core.domain import Story
from ainovel.core.field_templates import (
    copy_template_set,
    default_template_set,
    definition_from_entry,
    merge_template,
)
from ainovel.core.fields import FieldScope, FieldTarget, FieldTemplateSet
from ainovel.core.records import Entry


def read_fields(story: Story, target: FieldTarget) -> list[Entry]:
    scope = target.scope
    if scope == "story":
        return story.custom_fields
    if scope in ("world", "preferences", "writer"):
        return getattr(story, scope).entries
    if scope in ("character", "state"):
        if not any(character.id == target.entity_id for character in story.characters):
            raise ValueError("字段目标角色不存在")
        snapshot = story.snapshot(target.entity_id)
        return snapshot.profile.entries if scope == "character" else snapshot.state.entries
    entities = story.chapters if scope == "chapter" else story.scenes
    entity = next((item for item in entities if item.id == target.entity_id), None)
    if entity is None:
        raise ValueError("字段目标不存在")
    return entity.custom_fields


def targets_for(story: Story, scope: FieldScope) -> list[FieldTarget]:
    if scope in ("character", "state", "chapter", "scene"):
        items = story.characters if scope in ("character", "state") else story.chapters if scope == "chapter" else story.scenes
        return [FieldTarget(scope=scope, entity_id=item.id) for item in items]
    return [FieldTarget(scope=scope)]


def story_schema(story: Story) -> FieldTemplateSet:
    if story.field_template_set is not None:
        return story.field_template_set.model_copy(deep=True)
    # Legacy novels derive an initial structure from their actual saved materials.
    result = default_template_set()
    for template in result.templates:
        targets = targets_for(story, template.scope)
        if targets:
            template.fields = [definition_from_entry(entry) for entry in read_fields(story, targets[0])]
    return copy_template_set(result, f"{story.title} · 本书字段结构")


def ensure_story_schema(story: Story) -> None:
    if story.field_template_set is None:
        story.field_template_set = story_schema(story)


def initial_fields(story: Story, target: FieldTarget, entries: list[Entry]) -> list[Entry]:
    ensure_story_schema(story)
    template = next((item for item in story.field_template_set.templates if item.scope == target.scope), None)
    return merge_template(entries, template, target.entity_id or uuid5(story.id, target.scope)) if template else entries


def export_story_schema(story: Story, name: str, description: str, targets: list[FieldTarget]) -> FieldTemplateSet:
    result = copy_template_set(story_schema(story), name)
    result.description = description
    # Singleton materials always export their actual structure, including deletions.
    selected = [FieldTarget(scope=scope) for scope in ("story", "world", "preferences", "writer")]
    selected = [target for target in selected if target.scope not in {item.scope for item in targets}] + targets
    for target in selected:
        template = next(item for item in result.templates if item.scope == target.scope)
        template.fields = [definition_from_entry(entry) for entry in read_fields(story, target)]
    return result
