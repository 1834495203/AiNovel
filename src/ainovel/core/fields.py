"""Reusable field definitions. Templates contain definitions, never story values."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from ainovel.core.base import Model

FieldType = Literal["text", "number", "boolean", "enum", "object", "list", "reference"]
FieldScope = Literal["story", "world", "character", "state", "preferences", "writer", "chapter", "scene"]


class FieldDefinition(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    type: FieldType = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    children: list["FieldDefinition"] = Field(default_factory=list)
    item: "FieldDefinition | None" = None
    allow_extra: bool = True

    @model_validator(mode="after")
    def valid_structure(self) -> "FieldDefinition":
        if self.type == "enum" and (not self.options or len(set(self.options)) != len(self.options)):
            raise ValueError("枚举必须有不重复的选项")
        if self.type != "object" and self.children:
            raise ValueError("只有对象字段可以定义子字段")
        if self.type != "list" and self.item is not None:
            raise ValueError("只有列表字段可以定义列表项")
        ids = [child.id for child in self.children]
        if len(ids) != len(set(ids)):
            raise ValueError("子字段 ID 重复")
        return self


class FieldTemplate(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    scope: FieldScope
    fields: list[FieldDefinition] = Field(default_factory=list)
    builtin: bool = False
    parent_id: UUID | None = None

    @model_validator(mode="after")
    def unique_definitions(self) -> "FieldTemplate":
        seen: set[UUID] = set()

        def visit(items: list[FieldDefinition]) -> None:
            for item in items:
                if item.id in seen:
                    raise ValueError("模板内字段定义 ID 必须唯一")
                seen.add(item.id)
                visit(item.children)
                if item.item:
                    visit([item.item])

        visit(self.fields)
        return self


class FieldTarget(Model):
    scope: FieldScope
    entity_id: UUID | None = None

    @model_validator(mode="after")
    def valid_target(self) -> "FieldTarget":
        if (self.scope in ("character", "state", "chapter", "scene")) != (self.entity_id is not None):
            raise ValueError("角色、状态、章节和幕需要实体 ID；其他资料不指定实体 ID")
        return self


class FieldTemplateSet(Model):
    """A complete starting structure, copied into each novel instead of live-bound."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    templates: list[FieldTemplate] = Field(default_factory=list, max_length=8)
    builtin: bool = False
    parent_id: UUID | None = None
    replaces_id: UUID | None = None

    @model_validator(mode="after")
    def unique_scopes(self) -> "FieldTemplateSet":
        scopes = [item.scope for item in self.templates]
        if len(scopes) != len(set(scopes)):
            raise ValueError("一套小说模板中每类资料只能有一个字段结构")
        return self


class SaveFieldTemplateSetRequest(Model):
    kind: Literal["save_field_template_set"] = "save_field_template_set"
    template_set: FieldTemplateSet
    replace_template_set_id: UUID | None = None


class InitializeStoryTemplateSetRequest(Model):
    kind: Literal["initialize_story_template_set"] = "initialize_story_template_set"
    template_set_id: UUID


class UpdateStoryFieldSchemaRequest(Model):
    kind: Literal["update_story_field_schema"] = "update_story_field_schema"
    templates: list[FieldTemplate] = Field(max_length=8)

    @model_validator(mode="after")
    def unique_scopes(self) -> "UpdateStoryFieldSchemaRequest":
        FieldTemplateSet(name="本书字段结构", templates=self.templates)
        return self


class ExportStoryFieldSchemaRequest(Model):
    """Read-only projection; the caller saves it as an independent library command."""

    revision_id: UUID
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    targets: list[FieldTarget] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_scopes(self) -> "ExportStoryFieldSchemaRequest":
        scopes = [target.scope for target in self.targets]
        if len(scopes) != len(set(scopes)):
            raise ValueError("导出每类资料只能选择一个来源")
        return self


class SaveFieldTemplateRequest(Model):
    kind: Literal["save_field_template"] = "save_field_template"
    template: FieldTemplate


class ApplyFieldTemplateRequest(Model):
    kind: Literal["apply_field_template"] = "apply_field_template"
    template_id: UUID
    targets: list[FieldTarget] = Field(min_length=1, max_length=200)
