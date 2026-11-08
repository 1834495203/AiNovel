"""Template contracts for the existing role and prose projections."""

from functools import lru_cache
from typing import Literal

from pydantic import Field

from ainovel.core.base import Model
from ainovel.core.entities import Entity
from ainovel.core.gm_context import GmContext, RewindContext


class ContextEvent(Model):
    kind: Literal["gm", "gm_direction", "role", "enter", "leave", "self_edit"]
    actor_id: str
    text: str


class RoleSceneHistory(Model):
    scene: str
    events: list[ContextEvent]


class RoleContext(Model):
    self: Entity = Field(description="当前角色自己的可见资料。")
    present: list[Entity] = Field(description="其他在场角色的公开资料。")
    history: list[RoleSceneHistory] = Field(description="该角色有权接收的历史事件，按幕分组。")
    direction: str = Field(description="本次 GM 调度给此角色的提示。")


class WritingEvent(ContextEvent):
    audience: list[str] | None = Field(default=None, description="调度和字段变化事件的接收角色 ID。")


class SceneData(Model):
    scene: Entity = Field(description="幕标识与名称，旧幕的自定义字段供已保存的模板读取。")
    events: list[WritingEvent] = Field(description="本幕演绎记录，按发生顺序排列。")
    text: str = Field(description="上一版正文，首次生成时为空。")


class WritingContext(SceneData):
    chapter: Entity = Field(description="当前幕所属章节的章设定，含全部自定义字段。")
    characters: list[Entity] = Field(description="本幕参与角色的幕末状态。")
    previous_requirements: str = Field(description="上一版的写作要求。")
    writing_mode: Literal["generate", "rewrite"]
    requirements: str = Field(description="本次生成或重写的要求。")


class CreationContext(Model):
    title: str = Field(description="作品名称。")
    world: list[Entity] = Field(description="当前世界观资料。")
    characters: list[Entity] = Field(description="当前角色资料。")
    creation: list[Entity] = Field(description="全书创作要求。")


class WritingChatContext(Model):
    scene: str = Field(description="当前幕名称。")
    revision: str = Field(description="当前正文修订标识。")
    paragraph_count: int = Field(description="当前正文段落数。")
    has_prose: bool = Field(description="当前幕是否已有正文。")
    creation: list[Entity] = Field(description="全书创作要求。")


class SummaryContext(Model):
    chapter: str = Field(description="待摘要章节名称。")
    scenes: list[SceneData] = Field(description="本章各幕名称、演绎和正文。")


class SharedGmEvent(WritingEvent):
    id: str
    position: int


class SharedGmScene(SceneData):
    events: list[SharedGmEvent]
    channel: Literal["character_group"]
    audience: list[str]


class SharedGmContext(CreationContext):
    current_chapter: Entity = Field(description="当前幕所属章节的章设定。")
    chapter_scenes: list[Entity]
    current_scene: SharedGmScene
    present: list[str]
    present_count: int
    timeline: RewindContext
    chapters: list[dict]


CONTEXT_MODELS = {
    "creation": CreationContext, "gm": GmContext, "role": RoleContext,
    "writing": WritingContext, "writing_chat": WritingChatContext, "summary": SummaryContext,
}


@lru_cache
def variable_docs(mode: str, shared: bool = False) -> dict[str, dict[str, str]]:
    schema = (SharedGmContext if shared else CONTEXT_MODELS[mode]).model_json_schema()
    definitions = schema.get("$defs", {})
    fields = {}

    def visit(properties, prefix):
        for key, field in properties.items():
            path = f"{prefix}.{key}"
            shape = next((item for item in field.get("anyOf", []) if item.get("type") != "null"), field)
            resolved = definitions.get(shape.get("$ref", "").rsplit("/", 1)[-1], shape)
            items = resolved.get("items")
            element = definitions.get(items.get("$ref", "").rsplit("/", 1)[-1], items) if items else None
            kind = resolved.get("type", "any")
            if "enum" in resolved:
                kind = " | ".join(map(str, resolved["enum"]))
            if element:
                kind = f"array[{element.get('type', 'any')}]"
            fields[path] = {
                "type": kind + (" | null" if shape is not field else ""),
                "description": field.get("description", ""),
            }
            visit((element or resolved).get("properties", {}), path + ("[]" if items else ""))

    visit(schema.get("properties", {}), "data")
    return fields
