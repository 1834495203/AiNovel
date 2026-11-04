"""The shipped classifications are editable template definitions, not storage columns."""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ainovel.core.fields import FieldDefinition, FieldTemplate, FieldTemplateSet
from ainovel.core.records import (
    Entry,
)


def definition_id(scope: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ainovel:field:{scope}:{key}")


DEFAULT_FIELDS = {scope: [] for scope in ("story", "world", "character", "state", "preferences", "writer", "chapter", "scene")}

LABELS = {
    "story": "小说资料",
    "world": "世界观",
    "character": "角色档案",
    "state": "角色状态",
    "preferences": "创作偏好",
    "writer": "创作工作区",
    "chapter": "章节资料",
    "scene": "幕资料",
}

def default_templates() -> list[FieldTemplate]:
    return [FieldTemplate(id=uuid5(NAMESPACE_URL, f"ainovel:default-template:{scope}"),
        name=f"默认 · {label}", scope=scope, builtin=True, fields=[])
        for scope, label in LABELS.items()]


def instantiate(definition: FieldDefinition, namespace: UUID | None = None) -> Entry:
    return Entry(
        id=uuid5(namespace, str(definition.id)) if namespace else uuid4(),
        name=definition.name,
        definition=definition.model_copy(deep=True),
        children=[instantiate(child, namespace) for child in definition.children],
    )


def merge_template(entries: list[Entry], template: FieldTemplate, namespace: UUID) -> list[Entry]:
    """Add missing definitions without replacing any existing value or pinned definition."""
    result = [entry.model_copy(deep=True) for entry in entries]
    for definition in template.fields:
        found = next(
            (entry for entry in result if entry.definition and entry.definition.id == definition.id), None
        )
        if found is None:
            result.append(instantiate(definition, namespace))
    return result


def default_template_set() -> FieldTemplateSet:
    return FieldTemplateSet(
        id=uuid5(NAMESPACE_URL, "ainovel:default-template-set"), name="默认小说模板",
        description="世界观、角色、偏好、创作计划、章节和幕的完整初始化结构。",
        templates=default_templates(), builtin=True,
    )


def copy_template_set(source: FieldTemplateSet, name: str | None = None) -> FieldTemplateSet:
    """Freeze an independent full copy; no future operation resolves a library ID."""
    result = source.model_copy(deep=True)
    result.id = uuid4()
    result.parent_id = source.id
    result.name = name or source.name
    result.builtin = False
    result.replaces_id = None
    existing = {item.scope for item in result.templates}
    result.templates.extend(item for item in default_templates() if item.scope not in existing)
    for template in result.templates:
        template.parent_id = template.id
        template.id = uuid4()
        template.builtin = False
    return result


def definition_from_entry(entry: Entry) -> FieldDefinition:
    """Extract current structure, never filled values, notes or source scene IDs."""
    definition = entry.definition.model_copy(deep=True) if entry.definition else FieldDefinition(
        id=entry.id, name=entry.name,
        type="object" if entry.children else "boolean" if isinstance(entry.value, bool)
        else "number" if isinstance(entry.value, (int, float)) else "text",
    )
    definition.name = entry.name
    if definition.type == "object":
        definition.children = [definition_from_entry(child) for child in entry.children]
    elif definition.type == "list" and entry.children and definition.item:
        # List item names/values describe story content, not a new field name.
        item = definition_from_entry(entry.children[0])
        item.name = definition.item.name
        definition.item = item
    return definition
