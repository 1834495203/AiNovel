"""Explicit field configurations used by current tests, never application defaults."""

from ainovel.core.field_templates import definition_id
from ainovel.core.field_values import encode
from ainovel.core.fields import FieldDefinition
from ainovel.core.records import Entry, WorldSetting, WriterWorkspace


def make_world(text):
    return WorldSetting(
        entries=[
            Entry(
                name="世界观简介",
                value=text,
                definition=FieldDefinition(id=definition_id("world", "premise"), name="世界观简介"),
            )
        ]
    )


def make_workspace(**values):
    keys = (
        "mainlines",
        "subplots",
        "foreshadows",
        "outline",
        "future_directions",
        "notes",
        "drafts",
        "progress",
        "current_user_intent",
    )
    entries = []
    extras = values.pop("custom_fields", [])
    for key in keys:
        value = values.get(key, "" if key in ("progress", "current_user_intent") else [])
        if isinstance(value, list):
            value = [
                x.model_dump(mode="json", round_trip=True) if hasattr(x, "model_dump") else x for x in value
            ]
        entry = encode(value, key, "test:" + key)
        entry.definition.id = definition_id("writer", key)
        entries.append(entry)
    return WriterWorkspace(entries=[*entries, *extras])


def story_from_proposal(*, proposal, **kwargs):
    from ainovel.core.domain import Story

    return Story(title=proposal.title, world=proposal.world, preferences=proposal.preferences, **kwargs)
