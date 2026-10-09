"""Old fixed-field JSON decoding only. Current schemas remain small and open."""

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from ainovel.core.base import Model
from ainovel.core.records import CollectionPatch, Entry


class LegacyField(Model):
    name: str
    label: str
    kind: Literal["scalar", "replace", "collection"] = "collection"


WORLD_FIELDS = tuple(
    LegacyField(name=name, label=label)
    for name, label in (
        ("rules", "规则"),
        ("places", "地点"),
        ("factions", "组织"),
        ("history", "历史"),
        ("custom_fields", ""),
    )
)
PROFILE_FIELDS = tuple(
    LegacyField(name=name, label=label, kind=kind)
    for name, label, kind in (
        ("background", "背景", "collection"),
        ("personality", "性格", "collection"),
        ("values", "价值观", "collection"),
        ("speech_pattern", "表达方式", "scalar"),
        ("fears", "旧档案中的恐惧", "replace"),
        ("custom_fields", "", "collection"),
    )
)
STATE_FIELDS = tuple(
    LegacyField(name=name, label=label, kind=kind)
    for name, label, kind in (
        ("emotion", "情绪", "scalar"),
        ("thoughts", "想法", "replace"),
        ("outward_attitude", "外在表现", "scalar"),
        ("internal_attitude", "内在感受", "scalar"),
        ("positions", "当时的表达与行动", "replace"),
        ("beliefs", "信念", "collection"),
        ("opinions", "观点", "collection"),
        ("knowledge", "已知经历", "collection"),
        ("short_term_goals", "近期打算", "collection"),
        ("long_term_goals", "长远打算", "collection"),
        ("custom_fields", "", "collection"),
    )
)


def stable_id(scope: str, identifier: object) -> UUID:
    if scope:
        return uuid5(NAMESPACE_URL, scope + ":" + str(identifier))
    try:
        return UUID(str(identifier))
    except ValueError:
        return uuid5(NAMESPACE_URL, "legacy-entry:" + str(identifier))


def entry_from_legacy(value: object, scope: str, label: str = "") -> Entry:
    if isinstance(value, Model):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        raw = value.copy()
        identity = raw.pop("id", raw.get("name", raw.get("topic", "旧条目")))
        original_name = str(raw.pop("name", raw.pop("topic", "旧条目")))
        entry = Entry(
            id=stable_id(scope, identity),
            name=(label + " · " if label else "") + original_name,
            value=raw.pop("value", None),
            description=raw.pop("description", ""),
            reason=raw.pop("reason", ""),
            source_scene_id=raw.pop("source_scene_id", None),
        )
        children = [*raw.pop("children", []), *raw.pop("custom_fields", [])]
        entry.children = [entry_from_legacy(child, "", "") for child in children]
        for key, item in raw.items():
            if item is not None:
                entry.children.append(free_value(item, key, scope + ":" + str(identity) + ":" + key))
        return entry
    return Entry(
        id=uuid5(NAMESPACE_URL, scope + ":" + str(value)), name=(label + " · " if label else "") + str(value)
    )


def free_value(value: object, name: str, scope: str) -> Entry:
    entry = Entry(id=uuid5(NAMESPACE_URL, scope), name=name)
    if isinstance(value, Model):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        entry.children = [free_value(item, key, scope + ":" + key) for key, item in value.items()]
    elif isinstance(value, list):
        entry.children = [
            entry_from_legacy(item, scope + ":" + str(index)) for index, item in enumerate(value)
        ]
    elif isinstance(value, (str, int, float, bool)) or value is None:
        entry.value = value
    else:
        entry.value = str(value)
    return entry


def migrate_entries(
    value: object, fields: tuple[LegacyField, ...], scope: str, patch: bool = False
) -> object:
    if not isinstance(value, dict) or not any(field.name in value for field in fields):
        return value
    result = value.copy()
    current = CollectionPatch[Entry].model_validate(result.get("entries", {})) if patch else None
    entries = [] if patch else [Entry.model_validate(item) for item in result.get("entries", [])]
    for field in fields:
        if field.name not in result:
            continue
        raw = result.pop(field.name)
        if raw is None:
            continue
        field_scope = scope + "." + field.name if field.label else ""
        additions: list[Entry] = []
        removals: list[UUID] = []
        if field.kind != "collection":
            identity = uuid5(NAMESPACE_URL, field_scope)
            if raw:
                additions = [free_value(raw, field.label, field_scope)]
            elif patch:
                removals = [identity]
        else:
            if isinstance(raw, CollectionPatch):
                raw = raw.model_dump(mode="json")
            if patch and isinstance(raw, dict):
                additions = [
                    entry_from_legacy(item, field_scope, field.label) for item in raw.get("upsert", [])
                ]
                removals = [stable_id(field_scope, item) for item in raw.get("remove", [])]
            else:
                additions = [
                    entry_from_legacy(item, field_scope, field.label)
                    for item in (raw if isinstance(raw, list) else [raw])
                    if item
                ]
        if current is not None:
            current.upsert.extend(additions)
            current.remove.extend(removals)
        else:
            entries.extend(additions)
    result["entries"] = current.model_dump() if current is not None else entries
    return result
