"""Editable, identified records: human explanations remain text, relationships remain typed."""

from typing import Generic, Literal, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import Field, model_validator

from ainovel.core.base import Model


class Entry(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    value: str | int | float | bool | None = None
    description: str = ""
    reason: str = ""
    children: list["Entry"] = Field(default_factory=list)
    source_scene_id: UUID | None = None

    def text(self) -> str:
        parts = [self.name, str(self.value) if self.value is not None else "", self.description]
        return " ".join([*parts, *[child.text() for child in self.children]]).strip()


# Older SDK/checkpoint names remain readable; new domain containers use Entry.
CustomField = Entry


class Record(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    description: str = ""
    reason: str = ""
    source_scene_id: UUID | None = None
    custom_fields: list[CustomField] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_text(cls, value: object) -> object:
        if isinstance(value, str):
            # Old string fields acquire stable IDs so reading the same version is deterministic.
            return {"id": uuid5(NAMESPACE_URL, cls.__name__ + ":" + value), "name": value}
        return value


T = TypeVar("T", bound=Model)


class CollectionPatch(Model, Generic[T]):
    upsert: list[T] = Field(default_factory=list)
    remove: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def unambiguous(self) -> "CollectionPatch[T]":
        ids = [item.id for item in self.upsert]
        if len(set(ids)) != len(ids) or set(ids).intersection(self.remove):
            raise ValueError("增量修改存在重复或同时删除的条目")
        return self


def merge_records(items: list[T], patch: CollectionPatch[T]) -> list[T]:
    result = [item.model_copy(deep=True) for item in items if item.id not in patch.remove]
    for update in patch.upsert:
        index = next((index for index, item in enumerate(result) if item.id == update.id), None)
        if index is None:
            result.append(update.model_copy(deep=True))
        else:
            result[index] = update.model_copy(deep=True)
    return result


class WorldSetting(Model):
    premise: str = ""
    entries: list[Entry] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_world(cls, value: object) -> object:
        from ainovel.core.legacy import WORLD_FIELDS, migrate_entries

        return {"premise": value} if isinstance(value, str) else migrate_entries(value, WORLD_FIELDS, "world")


class WorldPatch(Model):
    premise: str | None = None
    entries: CollectionPatch[Entry] = Field(default_factory=CollectionPatch[Entry])

    @model_validator(mode="before")
    @classmethod
    def read_legacy_patch(cls, value: object) -> object:
        from ainovel.core.legacy import WORLD_FIELDS, migrate_entries

        return migrate_entries(value, WORLD_FIELDS, "world", patch=True)

    def apply(self, world: WorldSetting) -> WorldSetting:
        result = world.model_copy(deep=True)
        if self.premise is not None:
            result.premise = self.premise
        result.entries = merge_records(result.entries, self.entries)
        return result


class Preference(Record):
    origin: Literal["explicit", "inferred"] = "explicit"
    confidence: float = Field(default=1, ge=0, le=1)
    active: bool = True


class ReaderPreferences(Model):
    likes: list[Preference] = Field(default_factory=list)
    dislikes: list[Preference] = Field(default_factory=list)
    custom_fields: list[CustomField] = Field(default_factory=list)


class PreferencePatch(Model):
    likes: CollectionPatch[Preference] = Field(default_factory=CollectionPatch[Preference])
    dislikes: CollectionPatch[Preference] = Field(default_factory=CollectionPatch[Preference])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])

    def apply(self, preferences: ReaderPreferences) -> ReaderPreferences:
        return ReaderPreferences(
            likes=merge_records(preferences.likes, self.likes),
            dislikes=merge_records(preferences.dislikes, self.dislikes),
            custom_fields=merge_records(preferences.custom_fields, self.custom_fields),
        )


class PlotThread(Record):
    status: Literal["planned", "active", "resolved", "abandoned"] = "planned"
    progress: str = ""
    next_steps: list[str] = Field(default_factory=list)
    character_ids: list[UUID] = Field(default_factory=list)
    depends_on: list[UUID] = Field(default_factory=list)


class Foreshadow(Record):
    status: Literal["planned", "planted", "paid_off", "dropped"] = "planned"
    payoff: str = ""
    planted_scene_id: UUID | None = None
    paid_off_scene_id: UUID | None = None


class OutlineBeat(Record):
    order: int = Field(default=0, ge=0)
    status: Literal["planned", "active", "completed", "abandoned"] = "planned"
    thread_id: UUID | None = None


class WriterNote(Record):
    category: str = ""


class WorkingDraft(Record):
    kind: Literal["scene", "outline", "idea"] = "idea"
    content: str = ""
    status: Literal["draft", "used", "discarded"] = "draft"


class WriterWorkspace(Model):
    mainlines: list[PlotThread] = Field(default_factory=list)
    subplots: list[PlotThread] = Field(default_factory=list)
    foreshadows: list[Foreshadow] = Field(default_factory=list)
    outline: list[OutlineBeat] = Field(default_factory=list)
    future_directions: list[PlotThread] = Field(default_factory=list)
    notes: list[WriterNote] = Field(default_factory=list)
    drafts: list[WorkingDraft] = Field(default_factory=list)
    progress: str = ""
    current_user_intent: str = ""
    custom_fields: list[CustomField] = Field(default_factory=list)


class WriterPatch(Model):
    mainlines: CollectionPatch[PlotThread] = Field(default_factory=CollectionPatch[PlotThread])
    subplots: CollectionPatch[PlotThread] = Field(default_factory=CollectionPatch[PlotThread])
    foreshadows: CollectionPatch[Foreshadow] = Field(default_factory=CollectionPatch[Foreshadow])
    outline: CollectionPatch[OutlineBeat] = Field(default_factory=CollectionPatch[OutlineBeat])
    future_directions: CollectionPatch[PlotThread] = Field(default_factory=CollectionPatch[PlotThread])
    notes: CollectionPatch[WriterNote] = Field(default_factory=CollectionPatch[WriterNote])
    drafts: CollectionPatch[WorkingDraft] = Field(default_factory=CollectionPatch[WorkingDraft])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    progress: str | None = None
    current_user_intent: str | None = None

    def apply(self, workspace: WriterWorkspace) -> WriterWorkspace:
        result = workspace.model_copy(deep=True)
        for name in (
            "mainlines",
            "subplots",
            "foreshadows",
            "outline",
            "future_directions",
            "notes",
            "drafts",
            "custom_fields",
        ):
            setattr(result, name, merge_records(getattr(result, name), getattr(self, name)))
        if self.progress is not None:
            result.progress = self.progress
        if self.current_user_intent is not None:
            result.current_user_intent = self.current_user_intent
        return result


class StoryMetadataPatch(Model):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    world: WorldPatch = Field(default_factory=WorldPatch)
    preferences: PreferencePatch = Field(default_factory=PreferencePatch)
    writer: WriterPatch = Field(default_factory=WriterPatch)
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    reason: str = ""
