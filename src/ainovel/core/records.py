"""Editable, identified records: human explanations remain text, relationships remain typed."""

from typing import Generic, Literal, TypeVar
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from ainovel.core.base import Model
from ainovel.core.fields import FieldDefinition


class Entry(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    value: str | int | float | bool | None = None
    description: str = ""
    reason: str = ""
    children: list["Entry"] = Field(default_factory=list)
    source_scene_id: UUID | None = None
    definition: FieldDefinition | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "Entry":
        ids = [child.id for child in self.children]
        if len(ids) != len(set(ids)):
            raise ValueError("同一字段下的子条目 ID 不可重复")
        definition = self.definition
        if definition is None:
            return self
        if definition.type in ("object", "list"):
            if self.value is not None:
                raise ValueError("对象和列表通过子字段保存内容")
            if definition.type == "object":
                expected = {item.id: item for item in definition.children}
                actual = [child.definition.id for child in self.children if child.definition]
                if len(actual) != len(set(actual)):
                    raise ValueError("对象字段定义不可重复")
                if not definition.allow_extra and any(
                    child.definition is None or child.definition.id not in expected for child in self.children
                ):
                    raise ValueError("此对象不允许新增模板以外的字段")
                if any(
                    child.definition
                    and child.definition.id in expected
                    and child.definition != expected[child.definition.id]
                    for child in self.children
                ):
                    raise ValueError("子字段定义与对象模板不一致")
            if (
                definition.type == "list"
                and definition.item
                and any(child.definition != definition.item for child in self.children)
            ):
                raise ValueError("列表项必须使用列表定义中的字段结构")
        else:
            if self.children:
                raise ValueError("标量字段不能包含子字段")
            if self.value is not None:
                valid = {
                    "text": isinstance(self.value, str),
                    "number": type(self.value) in (int, float),
                    "boolean": isinstance(self.value, bool),
                    "enum": self.value in definition.options,
                    "reference": isinstance(self.value, str),
                }.get(definition.type, False)
                if not valid:
                    raise ValueError(f"字段 {self.name} 的值与 {definition.type} 类型不匹配")
                if definition.type == "reference":
                    UUID(str(self.value))
        return self

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
    entries: list[Entry] = Field(default_factory=list)


    @property
    def premise(self) -> str:
        from ainovel.core.field_values import get

        return str(get(self.entries, "world", "premise", "") or "\n".join(str(entry.value) for entry in self.entries if entry.value))

    @premise.setter
    def premise(self, value: str) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "world", "premise", value)


class WorldPatch(Model):
    premise: str | None = None
    entries: CollectionPatch[Entry] = Field(default_factory=CollectionPatch[Entry])


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
    entries: list[Entry] = Field(default_factory=list)


    @property
    def likes(self) -> list[Preference]:
        from ainovel.core.field_values import records

        return records(self.entries, "preferences", "likes", Preference)

    @likes.setter
    def likes(self, value: list[Preference]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "preferences", "likes", value)

    @property
    def dislikes(self) -> list[Preference]:
        from ainovel.core.field_values import records

        return records(self.entries, "preferences", "dislikes", Preference)

    @dislikes.setter
    def dislikes(self, value: list[Preference]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "preferences", "dislikes", value)

    @property
    def custom_fields(self) -> list[Entry]:
        from ainovel.core.field_templates import definition_id

        known = {definition_id("preferences", key): "list" for key in ("likes", "dislikes")}
        return [
            entry
            for entry in self.entries
            if entry.definition is None or known.get(entry.definition.id) != entry.definition.type
        ]

    @custom_fields.setter
    def custom_fields(self, value: list[Entry]) -> None:
        previous = {entry.id for entry in self.custom_fields}
        self.entries = [entry for entry in self.entries if entry.id not in previous] + value


class PreferencePatch(Model):
    entries: CollectionPatch[Entry] = Field(default_factory=CollectionPatch[Entry])
    likes: CollectionPatch[Preference] = Field(default_factory=CollectionPatch[Preference])
    dislikes: CollectionPatch[Preference] = Field(default_factory=CollectionPatch[Preference])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])

    def apply(self, preferences: ReaderPreferences) -> ReaderPreferences:
        result = preferences.model_copy(deep=True)
        for key in ("likes", "dislikes", "custom_fields"):
            patch = getattr(self, key)
            if patch.upsert or patch.remove:
                setattr(result, key, merge_records(getattr(result, key), patch))
        result.entries = merge_records(result.entries, self.entries)
        return result


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
    entries: list[Entry] = Field(default_factory=list)


    @property
    def mainlines(self) -> list[PlotThread]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "mainlines", PlotThread)

    @mainlines.setter
    def mainlines(self, value: list[PlotThread]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "mainlines", value)

    @property
    def subplots(self) -> list[PlotThread]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "subplots", PlotThread)

    @subplots.setter
    def subplots(self, value: list[PlotThread]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "subplots", value)

    @property
    def foreshadows(self) -> list[Foreshadow]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "foreshadows", Foreshadow)

    @foreshadows.setter
    def foreshadows(self, value: list[Foreshadow]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "foreshadows", value)

    @property
    def outline(self) -> list[OutlineBeat]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "outline", OutlineBeat)

    @outline.setter
    def outline(self, value: list[OutlineBeat]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "outline", value)

    @property
    def future_directions(self) -> list[PlotThread]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "future_directions", PlotThread)

    @future_directions.setter
    def future_directions(self, value: list[PlotThread]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "future_directions", value)

    @property
    def notes(self) -> list[WriterNote]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "notes", WriterNote)

    @notes.setter
    def notes(self, value: list[WriterNote]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "notes", value)

    @property
    def drafts(self) -> list[WorkingDraft]:
        from ainovel.core.field_values import records

        return records(self.entries, "writer", "drafts", WorkingDraft)

    @drafts.setter
    def drafts(self, value: list[WorkingDraft]) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "drafts", value)

    @property
    def progress(self) -> str:
        from ainovel.core.field_values import get

        return str(get(self.entries, "writer", "progress", "") or "")

    @progress.setter
    def progress(self, value: str) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "progress", value)

    @property
    def current_user_intent(self) -> str:
        from ainovel.core.field_values import get

        return str(get(self.entries, "writer", "current_user_intent", "") or "")

    @current_user_intent.setter
    def current_user_intent(self, value: str) -> None:
        from ainovel.core.field_values import put

        put(self.entries, "writer", "current_user_intent", value)

    @property
    def custom_fields(self) -> list[Entry]:
        from ainovel.core.field_templates import definition_id

        known = {definition_id("writer", key): "text" if key in ("progress", "current_user_intent") else "list" for key in ("mainlines", "subplots", "foreshadows", "outline", "future_directions", "notes", "drafts", "progress", "current_user_intent")}
        return [
            entry
            for entry in self.entries
            if entry.definition is None or known.get(entry.definition.id) != entry.definition.type
        ]

    @custom_fields.setter
    def custom_fields(self, value: list[Entry]) -> None:
        previous = {entry.id for entry in self.custom_fields}
        self.entries = [entry for entry in self.entries if entry.id not in previous] + value


class WriterPatch(Model):
    entries: CollectionPatch[Entry] = Field(default_factory=CollectionPatch[Entry])
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
            patch = getattr(self, name)
            if patch.upsert or patch.remove:
                setattr(result, name, merge_records(getattr(result, name), patch))
        if self.progress is not None:
            result.progress = self.progress
        if self.current_user_intent is not None:
            result.current_user_intent = self.current_user_intent
        result.entries = merge_records(result.entries, self.entries)
        return result


class StoryMetadataPatch(Model):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    world: WorldPatch = Field(default_factory=WorldPatch)
    preferences: PreferencePatch = Field(default_factory=PreferencePatch)
    writer: WriterPatch = Field(default_factory=WriterPatch)
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    reason: str = ""
