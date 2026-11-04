"""Typed discussion messages, tool contracts and current-story context."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from ainovel.core.base import Model
from ainovel.core.characters import Alias, ProfilePatch, Snapshot, StatePatch
from ainovel.core.context_layout import ContextField
from ainovel.core.fields import FieldTarget
from ainovel.core.records import (
    CollectionPatch,
    CustomField,
    Entry,
    ReaderPreferences,
    StoryMetadataPatch,
    WorldSetting,
    WriterWorkspace,
)
from ainovel.utils.time import now


class StartDiscussionRequest(Model):
    kind: Literal["start_discussion"] = "start_discussion"
    session_id: UUID = Field(default_factory=uuid4)
    mode: Literal["discussion", "planning"] = "discussion"
    title: str = Field(default="新讨论", min_length=1, max_length=100)


class DiscussionRequest(Model):
    mode: Literal["discussion", "planning"] = "discussion"
    kind: Literal["discussion"] = "discussion"
    session_id: UUID
    message: str = Field(min_length=1, max_length=12000)


class SearchStoryArguments(Model):
    """查询已确认的小说正文，不修改设定或正文。"""
    query: str = Field(min_length=1, max_length=2000, description="需要查找的人物、事件或线索；不要用来查询幕 ID，幕 ID 在幕列表中提供。")


class CharacterChange(Model):
    character_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=80)
    profile: ProfilePatch = Field(default_factory=ProfilePatch)
    state: StatePatch = Field(default_factory=StatePatch)
    aliases: CollectionPatch[Alias] = Field(default_factory=CollectionPatch[Alias])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    reason: str = Field(min_length=1)


class UpdateSettingsArguments(Model):
    patch: StoryMetadataPatch = Field(default_factory=StoryMetadataPatch)
    characters: list[CharacterChange] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)


class ReadStoryMaterialsArguments(Model):
    """按需读取某类资料或某一幕的完整设定，避免每次请求携带所有资料。"""
    scope: Literal["world", "preferences", "writer", "character", "state", "scene_plan"] = Field(description="需要读取的资料类别。")
    entity_id: UUID | None = Field(default=None, description="角色、状态或指定幕的 ID；全局类别留空。")
    offset: int = Field(default=0, ge=0, description="从第几个条目开始读取。")
    limit: int = Field(default=5, ge=1, le=20, description="本次最多读取的条目数。")


class ReadStoryMaterialsAction(Model):
    kind: Literal["read_story_materials"] = "read_story_materials"
    call_id: str = ""
    arguments: ReadStoryMaterialsArguments


class SearchStoryAction(Model):
    call_id: str = ""
    kind: Literal["search_story"] = "search_story"
    arguments: SearchStoryArguments


class UpdateSettingsAction(Model):
    kind: Literal["update_story_settings"] = "update_story_settings"
    arguments: UpdateSettingsArguments


class EditFieldsArguments(Model):
    target: FieldTarget
    patch: CollectionPatch[Entry]
    reason: str = Field(min_length=1, max_length=1000)


class EditFieldsAction(Model):
    kind: Literal["edit_fields"] = "edit_fields"
    arguments: EditFieldsArguments


class ScenePlanInput(Model):
    """新增一幕的简要剧情。计划不代表已经发生的事实。"""
    id: UUID = Field(default_factory=uuid4, description="新幕的唯一 ID；同批次后续幕可通过 parent_id 引用它")
    parent_id: UUID | None = Field(default=None, description="前一幕 ID；同一批次先提交父幕；同父幕的多个子幕形成分支。")
    title: str = Field(min_length=1, max_length=200, description="便于识别本幕的简短标题。")
    summary: str = Field(min_length=1, max_length=8000, description="本幕的核心事件、人物行动与结尾悬念，不填写正文。")
    character_names: list[str] = Field(default_factory=list, description="本幕涉及的角色姓名。")
    chapter_title: str = Field(default="", description="开启新章时使用的章节标题。")
    start_new_chapter: bool = Field(default=False, description="是否在本幕开始一个新章节。")


class ScenePlanUpdate(Model):
    """修改尚未生成正文的既有幕，不允许修改已写幕。"""
    id: UUID = Field(description="要修改的既有幕 ID，必须来自幕列表。")
    title: str | None = Field(default=None, min_length=1, max_length=200, description="新标题；省略则保留。")
    summary: str = Field(min_length=1, max_length=8000, description="修改后的完整简要剧情。")
    character_names: list[str] | None = Field(default=None, description="新的相关角色列表；省略则保留。")


class SaveScenePlansArguments(Model):
    """批量新增、修改幕或切换写作路线。只保存设定，不生成正文；已有正文的幕不可修改。"""
    create: list[ScenePlanInput] = Field(default_factory=list, max_length=40, description="按父幕在前的顺序排列的新幕列表。")
    update: list[ScenePlanUpdate] = Field(default_factory=list, max_length=40, description="需要一起修改的未写幕列表。")
    select_leaf_id: UUID | None = Field(default=None, description="选用路线的终点 ID；不能改变已写正文的前缀。新增时省略则选用最后一个新幕。")


class SaveScenePlansAction(Model):
    call_id: str = ""
    kind: Literal["save_scene_plans"] = "save_scene_plans"
    arguments: SaveScenePlansArguments


class ScenePlanBrief(Model):
    id: UUID
    parent_id: UUID | None = None
    title: str
    summary: str
    written: bool = False


class ReplyAction(Model):
    kind: Literal["reply"] = "reply"
    reply: str = Field(min_length=1, max_length=30000)


DiscussionAction = Annotated[
    SearchStoryAction | ReadStoryMaterialsAction | UpdateSettingsAction | EditFieldsAction | SaveScenePlansAction | ReplyAction, Field(discriminator="kind")
]


class DiscussionExcerpt(Model):
    """从已确认正文检索出的片段。"""
    title: str = Field(description="片段类别或来源标题。")
    text: str = Field(description="查到的正文片段。")
    summary: str = Field(default="", description="可用时提供片段摘要。")


class DiscussionCharacter(Model):
    id: UUID
    name: str
    snapshot: Snapshot


class DiscussionFields(Model):
    target: FieldTarget
    name: str
    entries: list[Entry]


class DiscussionStory(Model):
    title: str
    revision_id: UUID
    world: WorldSetting
    preferences: ReaderPreferences
    writer: WriterWorkspace
    characters: list[DiscussionCharacter]
    custom_fields: list[CustomField]
    recent_scenes: list[DiscussionExcerpt]
    field_documents: list[DiscussionFields] = Field(default_factory=list)


class DiscussionTurn(Model):
    role: Literal["user", "assistant"]
    content: str


class ToolResult(Model):
    """工具的公开返回值。success=false 时没有应用该次操作，模型可依据 message 修正。"""
    name: Literal["search_story", "read_story_materials", "save_scene_plans"] = Field(description="执行的工具名称。")
    success: bool = Field(default=True, description="本次工具调用是否成功。")
    message: str = Field(description="操作结果或可修正的错误说明；保存工具的结果在本轮结束时提交。")
    excerpts: list[DiscussionExcerpt] = Field(default_factory=list, description="查询工具返回的正文片段；保存工具为空。")
    materials: list[ContextField] = Field(default_factory=list, description="按需读取的资料内容，省略存储定义元数据。")
    next_offset: int | None = Field(default=None, description="还有未返回条目时，使用这个偏移继续读取。")
    affected_scene_ids: list[UUID] = Field(default_factory=list, description="本次成功创建或修改的幕 ID，可供后续工具引用。")


class DiscussionToolResult(ToolResult):
    """运行记录的内部消息配对元数据，不发送给模型。"""
    call_id: str = ""
    arguments_json: str = ""


class DiscussionContext(Model):
    planning_mode: bool = False
    scene_plans: list[ScenePlanBrief] = Field(default_factory=list)
    active_scene_leaf_id: UUID | None = None
    story: DiscussionStory
    history: list[DiscussionTurn]
    message: str
    tool_results: list[DiscussionToolResult] = Field(default_factory=list)


class DiscussionResult(Model):
    session_id: UUID
    reply: str
    read_revision_id: UUID
    settings_changed: bool = False
    updated_revision_id: UUID | None = None
    tool_results: list[DiscussionToolResult] = Field(default_factory=list)


class DiscussionSession(Model):
    mode: Literal["discussion", "planning"] = "discussion"
    id: UUID
    story_id: UUID
    title: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    message_count: int = 0
