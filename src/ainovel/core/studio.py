"""Scene-owned GM conversations and explicitly isolated rehearsal state."""

import json
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, field_validator, model_validator

from ainovel.core.base import Model
from ainovel.core.entities import Entity
from ainovel.core.prose_edits import ProseChange
from ainovel.core.studio_templates import TemplateFields
from ainovel.utils.time import now

TurnChannel = Literal["discussion", "writing"]


def identity() -> str:
    return str(uuid4())


def gm_discussion_text(content: str) -> str:
    """Unwrap the former discussion history format if it appears in a GM reply."""
    if not content.lstrip().startswith("{"):
        return content
    try:
        message = json.loads(content)
    except (ValueError, RecursionError):
        return content
    if (
        isinstance(message, dict)
        and message.keys() == {"channel", "speaker", "text"}
        and message["channel"] == "user_discussion"
        and message["speaker"] == "gm"
        and isinstance(message["text"], str)
    ):
        return message["text"]
    return content


class Patch(Model):
    op: Literal["add", "remove", "replace", "test"] = Field(description="JSON Patch 操作。")
    path: str = Field(description="相对目标的 JSON Pointer 路径，使用 ~0/~1 转义。")
    value: JsonValue = Field(default=None, description="add/replace/test 的值；remove 无需值。")


class NamedFieldEdit(Model):
    op: Literal["set_field", "remove_field"]
    field: str = Field(min_length=1, description="现有描述名称或变量名，按原名填写。")
    kind: Literal["description", "attribute"] | None = Field(
        default=None, description="同名时指定描述或变量。"
    )
    property: str = Field(
        default="", description="可选。指定要更新的属性，例如 how 或 visibility；默认更新字段值。"
    )
    value: JsonValue = Field(default=None, description="set_field 的新值；remove_field 删除整个字段。")

    @model_validator(mode="after")
    def validate_operation(self):
        if self.op == "set_field" and "value" not in self.model_fields_set:
            raise ValueError("set_field 需要提供 value")
        if self.op == "remove_field" and self.property:
            raise ValueError("remove_field 删除整个字段，不能指定 property")
        return self


class StageEvent(Model):
    id: str = Field(default_factory=identity)
    kind: Literal["gm", "gm_direction", "role", "enter", "leave", "self_edit"]
    actor_id: str = ""
    text: str
    audience: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)


class ConversationMessage(Model):
    id: str = Field(default_factory=identity)
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=now)

    @model_validator(mode="before")
    @classmethod
    def discard_retired_display_metadata(cls, value):
        # Read records written by the reverted presentation change without changing their content.
        if isinstance(value, dict) and "phase" in value:
            return {key: item for key, item in value.items() if key != "phase"}
        return value


class WritingMessage(ConversationMessage):
    changes: list[ProseChange] = Field(default_factory=list)
    checkpointed: bool = False


class StudioChapter(Model):
    entity: Entity
    summary: str = ""
    summary_stale: bool = True
    completed: bool = False


class PerformanceOperation(Model):
    id: str = Field(default_factory=identity, description="本幕内稳定的操作标识，由程序生成。")
    parent: str = Field(default="", description="本操作接在哪个操作之后；空字符串表示起始状态。")
    kind: str = Field(description="操作类型。")
    summary: str = Field(description="便于定位的简短操作说明。")
    origin: Literal["gm", "role", "user", "writer"] = Field(description="操作来源。")
    actor_id: str = Field(default="", description="角色操作所属的角色标识。")
    event_ids: list[str] = Field(default_factory=list, description="本次操作产生的群聊事件标识。")
    changes: list[Patch] = Field(description="程序记录的正向 JSON Patch，不包含逆操作。")
    created_at: datetime = Field(default_factory=now)


class PerformanceTimeline(Model):
    base: dict[str, JsonValue] = Field(description="开始记录时的起始状态，重放时不修改此值。")
    operations: list[PerformanceOperation] = Field(default_factory=list)
    head: str = Field(default="", description="当前选中的操作；空字符串表示起始状态。")
    state_hash: str = Field(description="当前物化状态校验值，防止未记录修改被覆盖。")


class StudioScene(Model):
    entity: Entity
    chapter_id: str
    confirmed: bool = False
    messages: list[ConversationMessage] = Field(default_factory=list)
    writing_messages: list[WritingMessage] = Field(default_factory=list)
    events: list[StageEvent] = Field(default_factory=list)
    abandoned: list[list[StageEvent]] = Field(default_factory=list)
    settings_patches: dict[str, list[Patch]] = Field(default_factory=dict)
    character_patches: dict[str, list[Patch]] = Field(default_factory=dict)
    character_snapshot: dict[str, Entity] | None = None
    text: str = ""
    writing_requirements: str = ""
    timeline: PerformanceTimeline | None = None

    @field_validator("messages")
    @classmethod
    def read_legacy_gm_messages(cls, messages: list[ConversationMessage]) -> list[ConversationMessage]:
        normalized = []
        for message in messages:
            if message.role == "assistant":
                content = gm_discussion_text(message.content)
                if content != message.content:
                    message = message.model_copy(update={"content": content})
            normalized.append(message)
        return normalized


class Compression(Model):
    full_chapters: int = Field(default=2, ge=0, le=100, description="最近多少章发送完整内容。")
    scene_chapters: int = Field(
        default=3, ge=0, le=100, description="再往前多少章发送幕设定和演绎，其余发送章节摘要。"
    )
    snapshot_interval: int = Field(default=5, ge=1, le=100, description="角色快照的确认幕间隔。")
    max_calls: int = Field(default=16, ge=1, le=100, description="一轮 GM 与角色合计模型调用预算。")


class StudioBranch(Model):
    id: str = Field(default_factory=identity)
    name: str
    scenes: list[StudioScene]
    chapters: list[StudioChapter]


class TurnCheckpoint(Model):
    scene_id: str = ""
    message_id: str
    version: int = 0
    before: dict[str, JsonValue]
    channel: TurnChannel = "discussion"


class GmTemplate(Model):
    id: str = Field(default_factory=identity)
    name: str
    source: str


class StudioNovel(Model):
    id: str = Field(default_factory=identity)
    version: int = 0
    title: str = "未命名小说"
    created: bool = False
    deleted: bool = False
    world: list[Entity] = Field(default_factory=list)
    characters: list[Entity] = Field(default_factory=list)
    creation: list[Entity] = Field(default_factory=list)
    template_fields: TemplateFields = Field(default_factory=TemplateFields)
    field_template_name: str = "空白字段"
    chapters: list[StudioChapter] = Field(default_factory=list)
    scenes: list[StudioScene] = Field(default_factory=list)
    branches: list[StudioBranch] = Field(default_factory=list)
    creation_messages: list[ConversationMessage] = Field(default_factory=list)
    compression: Compression = Field(default_factory=Compression)
    prompts: dict[str, str] = Field(default_factory=dict)
    context_source: str = "{{ data | tojson }}"
    gm_context_source: str | None = None
    gm_templates: list[GmTemplate] = Field(default_factory=list)
    active_gm_template_id: str | None = None
    busy: bool = False
    run_id: str = ""
    running: str = ""
    error: str = ""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    turn_checkpoint: TurnCheckpoint | None = None


class ToolOutcome(Model):
    success: bool = Field(default=True, description="操作是否成功。")
    message: str = Field(default="", description="结果或可修正的错误。")
    data: JsonValue = Field(default=None, description="操作产生的数据。")


class StudioAction(Model):
    name: str
    call_id: str = ""
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    text: str = ""


class StudioStep(Model):
    actions: list[StudioAction]
    text: str = ""
