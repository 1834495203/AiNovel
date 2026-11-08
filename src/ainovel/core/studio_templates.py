"""Field templates initialize independent Entity data; they are never live bindings."""

from uuid import uuid4

from pydantic import Field, model_validator

from ainovel.core.base import Model
from ainovel.core.entities import Entity, EntityInput


class TemplateFields(Model):
    world: EntityInput = Field(default_factory=lambda: EntityInput(name="世界观", type="world"))
    characters: EntityInput = Field(default_factory=lambda: EntityInput(name="角色", type="human"))
    creation: EntityInput = Field(default_factory=lambda: EntityInput(name="创作", type="creation"))
    chapters: EntityInput = Field(default_factory=lambda: EntityInput(name="章节", type="chapter"))

    @model_validator(mode="before")
    @classmethod
    def ignore_retired_scene_prototype(cls, data):
        if isinstance(data, dict) and "scenes" in data:
            data = {key: value for key, value in data.items() if key != "scenes"}
        return data


class StudioFieldTemplate(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 0
    name: str = Field(min_length=1, max_length=160)
    fields: TemplateFields = Field(default_factory=TemplateFields)


def initialize_entity(prototype: EntityInput, supplied: EntityInput) -> Entity:
    data = supplied.model_copy(deep=True)
    for field, key in (("description", "name"), ("special_attributes", "key")):
        merged = {getattr(item, key): item.model_copy(deep=True) for item in getattr(prototype, field)}
        for item in getattr(data, field):
            merged[getattr(item, key)] = item.model_copy(deep=True)
        setattr(data, field, list(merged.values()))
    return Entity(**data.model_dump())


def entity_fields(entity: Entity) -> EntityInput:
    return EntityInput.model_validate(entity.model_dump(exclude={"id"}))
