"""Domain contracts. No untyped payload bags; snapshots and revisions are immutable records."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, computed_field, model_validator

from ainovel.core.base import Model
from ainovel.core.characters import (
    Alias,
    Character,
    CharacterPatch,
    CharacterSpec,
    CharacterState,
    Experience,
    Profile,
    ProfilePatch,
    Snapshot,
    StatePatch,
)
from ainovel.core.context_layout import default_context_source
from ainovel.core.discussion import (
    DiscussionRequest,
    DiscussionResult,
    DiscussionSession,
    StartDiscussionRequest,
)
from ainovel.core.fields import (
    ApplyFieldTemplateRequest,
    FieldTarget,
    FieldTemplate,
    FieldTemplateSet,
    InitializeStoryTemplateSetRequest,
    SaveFieldTemplateRequest,
    SaveFieldTemplateSetRequest,
    UpdateStoryFieldSchemaRequest,
)
from ainovel.core.records import (
    CollectionPatch,
    CustomField,
    Entry,
    ReaderPreferences,
    StoryMetadataPatch,
    WorldSetting,
    WriterPatch,
    WriterWorkspace,
)
from ainovel.core.text_edits import TextEdit
from ainovel.utils.time import now


class Direction(Model):
    id: UUID = Field(default_factory=uuid4)
    instruction: str = Field(min_length=1, max_length=4000)
    scope: Literal["scene", "chapter", "future", "global"] = "global"
    chapter_id: UUID | None = None
    scene_id: UUID | None = None
    hard: bool = False
    active: bool = True

    @model_validator(mode="after")
    def scope_has_target(self) -> "Direction":
        if self.scope == "chapter" and self.chapter_id is None:
            raise ValueError("chapter direction requires chapter_id")
        # scene_id=None means the next generated scene, consumed at commit.
        return self


class Scene(Model):
    id: UUID = Field(default_factory=uuid4)
    chapter_id: UUID
    title: str
    text: str
    summary: str
    character_patches: list[CharacterPatch] = Field(default_factory=list)
    source_command_id: UUID | None = None
    custom_fields: list[Entry] = Field(default_factory=list)


class Chapter(Model):
    id: UUID = Field(default_factory=uuid4)
    title: str
    summary: str = ""
    custom_fields: list[Entry] = Field(default_factory=list)


class CreationProposal(Model):
    """Temporary interview proposal; canonical Story has no setup container."""

    title: str = Field(default="新故事", min_length=1, max_length=200)
    world: WorldSetting = Field(default_factory=WorldSetting)
    characters: list[CharacterSpec] = Field(default_factory=list)
    preferences: ReaderPreferences = Field(default_factory=ReaderPreferences)
    custom_fields: list[CustomField] = Field(default_factory=list)


    @model_validator(mode="after")
    def unique_names(self) -> "CreationProposal":
        names = [character.name for character in self.characters]
        if len(names) != len(set(names)):
            raise ValueError("character names must be unique")
        return self




class PlannedScene(Model):
    id: UUID = Field(default_factory=uuid4)
    parent_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    plan: "Plan"


class PlanSceneRequest(Model):
    kind: Literal["plan_scene"] = "plan_scene"
    instruction: str = Field(min_length=1, max_length=8000)
    parent_id: UUID | None = None
    target_id: UUID | None = None


class SelectSceneBranchRequest(Model):
    kind: Literal["select_scene_branch"] = "select_scene_branch"
    leaf_id: UUID


class Story(Model):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    world: WorldSetting = Field(default_factory=WorldSetting)
    preferences: ReaderPreferences = Field(default_factory=ReaderPreferences)
    writer: WriterWorkspace = Field(default_factory=WriterWorkspace)
    characters: list[Character] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    planned_scenes: list[PlannedScene] = Field(default_factory=list)
    active_scene_leaf_id: UUID | None = None
    directions: list[Direction] = Field(default_factory=list)
    initial_snapshots: list[Snapshot] = Field(default_factory=list)
    custom_fields: list[CustomField] = Field(default_factory=list)
    field_template_set: FieldTemplateSet | None = None


    def snapshot(self, character_id: UUID, scene_id: UUID | None = None) -> Snapshot:
        limit = len(self.scenes)
        if scene_id is not None:
            limit = next(i + 1 for i, scene in enumerate(self.scenes) if scene.id == scene_id)
        character = next(character for character in self.characters if character.id == character_id)
        if character.introduced_scene_id is not None and scene_id is not None:
            if character.introduced_scene_id not in {scene.id for scene in self.scenes[:limit]}:
                raise ValueError("此角色在该阶段尚未出现")
        initial = next((item for item in self.initial_snapshots if item.character_id == character_id), None)
        if initial is None:
            raise ValueError("缺少角色初始状态")
        result = initial.model_copy(deep=True)
        for scene in self.scenes[:limit]:
            for patch in scene.character_patches:
                if patch.character_id == character_id:
                    result = result.apply(patch)
        return result

    def apply_metadata(self, patch: StoryMetadataPatch) -> None:
        if patch.title is not None:
            self.title = patch.title
        self.world = patch.world.apply(self.world)
        self.preferences = patch.preferences.apply(self.preferences)
        self.writer = patch.writer.apply(self.writer)
        from ainovel.core.records import merge_records

        self.custom_fields = merge_records(self.custom_fields, patch.custom_fields)


class Revision(Model):
    id: UUID = Field(default_factory=uuid4)
    story_id: UUID
    parent_id: UUID | None = None
    command_id: UUID
    reason: str
    story: Story
    created_at: datetime = Field(default_factory=now)


class Turn(Model):
    role: Literal["user", "assistant"]
    content: str


class InterviewRequest(Model):
    kind: Literal["interview"] = "interview"
    turns: list[Turn] = Field(default_factory=list, max_length=40)


class DraftFieldEdit(Model):
    key: Literal["title", "world", "preferences", "dislikes", "name", "profile", "background", "personality"]
    index: int = Field(default=0, ge=0)
    value: str = Field(max_length=8000)


class SetupDraft(Model):
    id: UUID
    title: str = Field(default="新故事草稿", max_length=200)
    input_text: str = Field(default="", max_length=8000)
    pending_input: str = Field(default="", max_length=8000)
    proposal: CreationProposal | None = Field(
        default=None
    )
    editing_setup: bool = False
    edits: list[DraftFieldEdit] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now)
    save_event_id: int = Field(default=0, ge=0)
    field_template_ids: list[UUID] = Field(default_factory=list)
    field_template_set_id: UUID | None = None


class SaveSetupDraftRequest(Model):
    kind: Literal["save_setup_draft"] = "save_setup_draft"
    draft: SetupDraft


class PurgeResult(Model):
    command_ids: list[UUID] = Field(default_factory=list)
    cache_ids: list[UUID] = Field(default_factory=list)


class SetSetupDraftDeletedRequest(Model):
    kind: Literal["set_setup_draft_deleted"] = "set_setup_draft_deleted"
    deleted: bool


class CreateStoryRequest(Model):
    kind: Literal["create_story"] = "create_story"
    proposal: CreationProposal
    confirmed: Literal[True]
    field_template_ids: list[UUID] = Field(default_factory=list)
    field_template_set_id: UUID | None = None


class GenerateRequest(Model):
    planned_scene_id: UUID | None = None
    kind: Literal["generate"] = "generate"
    instruction: str = Field(min_length=1, max_length=8000)
    prompt_templates: list["PromptTemplateSelection"] = Field(default_factory=list, max_length=15)
    # Retained for reading older persisted commands; chapter decisions now come from the planner.
    new_chapter: bool = Field(default=False, exclude=True, deprecated=True)


PromptTask = Literal["creation_interview", "scene_planning", "writing", "discussion", "character_chat"]
PROMPT_TASKS: tuple[PromptTask, ...] = ("creation_interview", "scene_planning", "writing", "discussion", "character_chat")


class PromptContextVariable(Model):
    name: str
    type: Literal["string", "boolean"]
    description: str


class PromptTemplateVersion(Model):
    context_source: str = Field(default_factory=default_context_source, max_length=12000, description="独立的 Jinja2 上下文布局源码；不包含 JSON Schema。")
    id: UUID
    scope: Literal["global", "story"]
    scope_id: UUID
    task: PromptTask
    name: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    guidance: str = Field(default="", max_length=4000)
    source_format: Literal["legacy_guidance", "jinja2"] = "legacy_guidance"
    source: str = Field(default="", max_length=25000)
    editor_source: str = Field(default="", max_length=30000)
    template_path: str = ""
    source_hash: str = ""
    context_variables: list[str] = Field(default_factory=list)
    context_schema: list[PromptContextVariable] = Field(default_factory=list)
    builtin: bool = False
    created_at: datetime = Field(default_factory=now)


class PromptTemplateBundle(Model):
    """Immutable command-local prompt sources pinned before the first model call."""

    templates: list[PromptTemplateVersion] = Field(default_factory=list, max_length=15)


class PromptTemplateSelection(Model):
    task: PromptTask
    version_id: UUID


class SavePromptTemplateRequest(Model):
    context_source: str | None = Field(default=None, max_length=12000, description="上下文 Jinja2 模板，与行为提示词分别保存。")
    kind: Literal["save_prompt_template"] = "save_prompt_template"
    action: Literal["save", "reset"] = "save"
    scope: Literal["global", "story"]
    task: PromptTask
    name: str = Field(default="自定义模板", min_length=1, max_length=80)
    guidance: str = Field(default="", max_length=4000)
    source: str | None = Field(default=None, max_length=25000)


class PromptTemplateTaskLibrary(Model):
    task: PromptTask
    label: str
    description: str
    template_path: str
    context_variables: list[str] = Field(default_factory=list)
    context_schema: list[PromptContextVariable] = Field(default_factory=list)
    editor_source: str = ""
    active: PromptTemplateVersion
    global_active: PromptTemplateVersion
    story_active: PromptTemplateVersion | None = None
    versions: list[PromptTemplateVersion] = Field(default_factory=list)


class PromptTemplateLibrary(Model):
    tasks: list[PromptTemplateTaskLibrary]


class PromptTemplatePreviewContext(Model):
    world_context: str = ""
    characters_context: str = ""
    preferences_context: str = ""
    writer_context: str = ""
    scene_plans_context: str = ""
    recent_story_context: str = ""
    task_context: str = ""

    schema_name: str = ""
    story_title: str = ""
    instruction: str = ""
    current_scene_summary: str = ""
    feedback: str = ""
    selected_text: str = ""
    draft_text: str = ""
    draft_summary: str = ""
    character_name: str = ""
    role_profile: str = ""
    discussion_message: str = ""
    conversation_history: str = ""
    tool_results: str = ""
    first_creation: bool = False
    quick_path: bool = False
    maintenance_due: bool = False


class PromptPreviewMessage(Model):
    role: Literal["system", "user"]
    content: str


class PromptTemplatePreviewRequest(Model):
    context_source: str | None = Field(default=None, max_length=12000)
    task: PromptTask
    source: str | None = Field(default=None, max_length=25000)
    guidance: str = Field(default="", max_length=4000)
    context: PromptTemplatePreviewContext = Field(default_factory=PromptTemplatePreviewContext)


class PromptTemplatePreview(Model):
    strategy_preview: str = ""
    note: str = ""
    rendered_prompt: str = ""
    rendered_messages: list[PromptPreviewMessage] = Field(default_factory=list)
    template_path: str = ""
    source_hash: str = ""
    context_variables: list[str] = Field(default_factory=list)
    context_schema: list[PromptContextVariable] = Field(default_factory=list)


class DraftSelection(Model):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    selected_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> "DraftSelection":
        if self.end <= self.start:
            raise ValueError("选区结束位置必须大于开始位置")
        return self


class ReviewDraftRequest(Model):
    text_only: bool = False
    kind: Literal["review_draft"] = "review_draft"
    generation_command_id: UUID
    expected_draft_version: int = Field(ge=1)
    decision: Literal["confirm", "revise", "discard"]
    feedback: str = Field(default="", max_length=8000)
    selection: DraftSelection | None = None

    @model_validator(mode="after")
    def revision_has_feedback(self) -> "ReviewDraftRequest":
        if self.decision == "revise" and not self.feedback.strip():
            raise ValueError("修改草稿需要说明意见")
        if self.decision != "revise" and (self.selection is not None or self.feedback):
            raise ValueError("确认或放弃草稿不能同时要求修改")
        return self


class DirectionRequest(Model):
    kind: Literal["direction"] = "direction"
    direction: Direction


class RewriteRequest(Model):
    kind: Literal["rewrite"] = "rewrite"
    scene_id: UUID | None = None
    chapter_id: UUID | None = None
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    selected_text: str = Field(min_length=1)
    instruction: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def one_target(self) -> "RewriteRequest":
        if (self.scene_id is None) == (self.chapter_id is None):
            raise ValueError("指定 scene_id 或 chapter_id 其中之一")
        return self


class AcceptRewriteRequest(Model):
    kind: Literal["accept_rewrite"] = "accept_rewrite"
    proposal_command_id: UUID


class RejectRewriteRequest(Model):
    kind: Literal["reject_rewrite"] = "reject_rewrite"
    proposal_command_id: UUID


class ResolveCharacterRequest(Model):
    kind: Literal["resolve_character"] = "resolve_character"
    generation_command_id: UUID
    decision: Literal["consent", "accept", "reject"]
    character: CharacterSpec | None = None


class RevertRequest(Model):
    kind: Literal["revert"] = "revert"
    target_revision_id: UUID


class ReindexRequest(Model):
    kind: Literal["reindex"] = "reindex"


class DeleteStoryRequest(Model):
    kind: Literal["delete_story"] = "delete_story"


class RestoreStoryRequest(Model):
    kind: Literal["restore_story"] = "restore_story"


class UndoRequest(Model):
    scope_id: UUID | None = None
    kind: Literal["undo"] = "undo"
    expected_operation_id: int = Field(gt=0)
    target_command_id: UUID | None = None


class RecoverCommandRequest(Model):
    kind: Literal["recover_command"] = "recover_command"
    target_command_id: UUID
    expected_event_id: int = Field(gt=0)
    decision: Literal["retry", "withdraw"] = "retry"


class CharacterChatRequest(Model):
    kind: Literal["character_chat"] = "character_chat"
    character_id: UUID
    scene_id: UUID
    session_id: UUID = Field(default_factory=uuid4)
    message: str = Field(min_length=1, max_length=8000)


class StartCharacterChatRequest(Model):
    kind: Literal["start_character_chat"] = "start_character_chat"
    character_id: UUID
    scene_id: UUID
    session_id: UUID = Field(default_factory=uuid4)


class StoryMaintenance(Model):
    patch: StoryMetadataPatch = Field(default_factory=StoryMetadataPatch)
    decision_summary: str = ""


class UpdateStoryRequest(Model):
    kind: Literal["update_story"] = "update_story"
    patch: StoryMetadataPatch
    character_patches: list[CharacterPatch] = Field(default_factory=list)
    character_edits: list["CharacterEdit"] = Field(default_factory=list)


class CharacterEdit(Model):
    character_id: UUID
    profile: ProfilePatch = Field(default_factory=ProfilePatch)
    aliases: CollectionPatch[Alias] = Field(default_factory=CollectionPatch[Alias])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    reason: str = ""


class EditFieldsRequest(Model):
    kind: Literal["edit_fields"] = "edit_fields"
    target: FieldTarget
    patch: CollectionPatch[Entry]
    reason: str = Field(default="更新自定义字段", min_length=1)


class ApprovalResponse(Model):
    decision: Literal["accept", "reject"]
    character: CharacterSpec | None = None


class CreationField(Model):
    key: Literal["world", "characters", "preferences", "dislikes"]
    label: str
    status: Literal["defined", "missing", "deferred"] = "missing"
    summary: str = ""
    required: bool = False


Request = Annotated[
    InterviewRequest
    | CreateStoryRequest
    | GenerateRequest
    | DirectionRequest
    | RewriteRequest
    | AcceptRewriteRequest
    | RejectRewriteRequest
    | ResolveCharacterRequest
    | RevertRequest
    | ReindexRequest
    | DeleteStoryRequest
    | RestoreStoryRequest
    | CharacterChatRequest
    | StartCharacterChatRequest
    | DiscussionRequest
    | StartDiscussionRequest
    | UndoRequest
    | RecoverCommandRequest
    | SaveSetupDraftRequest
    | SetSetupDraftDeletedRequest
    | PlanSceneRequest
    | SelectSceneBranchRequest
    | UpdateStoryRequest
    | ReviewDraftRequest
    | SavePromptTemplateRequest
    | SaveFieldTemplateRequest
    | ApplyFieldTemplateRequest
    | EditFieldsRequest
    | SaveFieldTemplateSetRequest
    | InitializeStoryTemplateSetRequest
    | UpdateStoryFieldSchemaRequest,
    Field(discriminator="kind"),
]


class CommandSubmission(Model):
    id: UUID = Field(default_factory=uuid4)
    actor: Literal["user"] = "user"
    story_id: UUID | None = None
    base_revision_id: UUID | None = None
    session_id: UUID | None = None
    payload: Request

    @property
    def operation_scope_id(self) -> UUID:
        if self.payload.kind in ("discussion", "start_discussion") and self.payload.mode == "planning":
            return self.story_id or self.payload.session_id
        if self.payload.kind in ("discussion", "start_discussion", "character_chat", "start_character_chat"):
            return self.payload.session_id
        if self.payload.kind == "undo" and self.payload.scope_id:
            return self.payload.scope_id
        return self.story_id or self.session_id or self.id

    @model_validator(mode="after")
    def revision_required(self) -> "CommandSubmission":
        if self.payload.kind == "set_setup_draft_deleted":
            if self.story_id is not None or self.base_revision_id is not None or self.session_id is None:
                raise ValueError("草稿删除或恢复需要独立 session_id")
            return self
        if self.payload.kind in ("save_field_template", "save_field_template_set"):
            if self.story_id is not None or self.base_revision_id is not None or self.session_id is None:
                raise ValueError("字段模板保存需要独立 session_id，不能指定小说版本")
            return self
        if self.payload.kind == "save_prompt_template":
            if self.payload.scope == "global":
                if self.story_id is not None or self.base_revision_id is not None or self.session_id is None:
                    raise ValueError("全局模板命令需要独立 session_id，不能指定小说版本")
            elif self.story_id is None or self.base_revision_id is None or self.session_id is not None:
                raise ValueError("作品模板命令需要 story_id 与 base_revision_id")
            return self
        if self.payload.kind == "save_setup_draft":
            if (
                self.story_id is not None
                or self.base_revision_id is not None
                or self.session_id != self.payload.draft.id
            ):
                raise ValueError("草稿保存需要一致的 session_id，不能指定小说版本")
            return self
        if self.payload.kind in ("undo", "recover_command") and self.story_id is None:
            if self.session_id is None or self.base_revision_id is not None:
                raise ValueError("会话撤回或恢复需要 session_id，不能指定小说版本")
            return self
        if self.payload.kind not in ("interview", "create_story"):
            if self.story_id is None or self.base_revision_id is None:
                raise ValueError("story_id and base_revision_id are required")
        elif self.story_id is not None or self.base_revision_id is not None:
            raise ValueError("interview/create_story must not carry a story revision")
        return self


class InterviewResult(Model):
    """构思访谈的回复与当前提案；不创建正式小说。"""
    reply: str = Field(description="面向用户的回复或必要的追问。")
    checklist: list[CreationField] = Field(default_factory=list, description="标明当前已定义、缺失或允许稍后定义的内容。")
    proposal: CreationProposal | None = Field(default=None, description="依据已知信息整理的当前提案，允许不完整，不虚构用户已确认的设定。")


class Intent(Model):
    goal: str
    route: Literal["fast", "deliberate"] = Field(default="deliberate", description="内部执行路线：fast 复用既有计划；deliberate 用于首次规划或重大方向变化，不是小说自定义字段。")
    route_reason: str = ""
    constraints: list[str] = Field(default_factory=list)
    use_retrieval: bool = True
    use_characters: bool = True
    retrieval_reason: str = ""
    characters_reason: str = ""


class CharacterNeed(Model):
    name: str = Field(description='拟引入人物的姓名。')
    role: str = Field(description='人物在故事中的作用。')
    reason: str = Field(description='本幕为何需要这个持续角色。')


class Plan(Model):
    """一幕的剧情计划。计划不是已发生事实，已有幕计划时正文直接复用。"""
    writer_patch: WriterPatch = Field(default_factory=WriterPatch, description='对已有创作资料的增量修改；无变化时保持空补丁。')
    chapter_plan: str = Field(description='当前章节的简要安排。')
    scene_plan: str = Field(description='本幕的核心事件、行动和悬念。')
    character_names: list[str] = Field(description='本幕相关的角色姓名。')
    new_character: CharacterNeed | None = Field(default=None, description='确需引入的持续角色及原因；没有则省略。')
    start_new_chapter: bool = Field(default=False, description='是否在本幕开启新章节。')
    chapter_title: str = Field(default="", description='开启新章时使用的标题。')



class Consultation(Model):
    character_id: UUID
    entries: list[Entry] = Field(default_factory=list)



class Draft(Model):
    """待用户确认的一幕正文及事实摘要，不包含字段 schema。"""
    title: str = Field(description="本幕正文标题。")
    text: str = Field(min_length=1, description="完整正文，保留段落和排版。")
    summary: str = Field(description="只概括正文实际发生事件的摘要。")


class WritingDraft(Model):
    generation_command_id: UUID
    version: int = Field(ge=1)
    content: Draft
    status: Literal["pending", "confirmed", "discarded"] = "pending"


class DraftReviewResponse(Model):
    text_only: bool = False
    decision: Literal["confirm", "revise", "discard"]
    execution_command_id: UUID
    feedback: str = ""
    selection: DraftSelection | None = None


class RevisionPlan(Model):
    goal: str
    replan: bool = Field(default=False, description="仅剧情结构或本幕目标大改时重新规划；默认复用原本幕计划")
    use_retrieval: bool = Field(default=False, description="原上下文不足时补充召回；默认保留并复用已有召回")
    use_characters: bool = Field(
        default=False, description="角色动机或行动变化需要重新访谈时启用；默认复用访谈结果"
    )
    review_required: bool = Field(default=False, description="重大改动需要独立一致性审阅时启用")
    decision_reason: str = ""
    instructions: list[str] = Field(default_factory=list)
    preserve_continuity: bool = True
    new_character: CharacterNeed | None = None
    character_names: list[str] = Field(default_factory=list)


class Issue(Model):
    category: Literal["intent", "direction", "character", "continuity", "prose"]
    severity: Literal["hard", "soft"]
    description: str


class JudgeResult(Model):
    """草稿审阅结果；格式由本协议定义，不要求小说拥有任何固定资料字段。"""
    user_intent_satisfied: bool = Field(description="是否满足本次用户要求。")
    direction_satisfied: bool = Field(description="是否遵守当前有效的创作约束。")
    issues: list[Issue] = Field(description="具体问题；没有问题时为空。")
    overall_score: float = Field(ge=0, le=10, description="本轮草稿质量评分。")
    should_rewrite: bool = Field(description="是否需要修订；违反硬约束或存在严重事实冲突时必须为真。")
    rewrite_instructions: list[str] = Field(description="可执行的正文修改建议；没有必要修改时为空。")

    @property
    def blocked(self) -> bool:
        return (
            self.should_rewrite
            or not self.user_intent_satisfied
            or not self.direction_satisfied
            or any(issue.severity == "hard" for issue in self.issues)
        )


class StateUpdate(Model):
    character_id: UUID = Field(description='发生变化的既有角色 ID。')
    state: StatePatch = Field(default_factory=StatePatch, description='当前状态字段的实际变化。')
    profile: "ProfilePatch" = Field(default_factory=lambda: ProfilePatch(), description='正文有明确依据的稳定档案变化。')
    aliases: "CollectionPatch[Alias]" = Field(default_factory=lambda: CollectionPatch[Alias](), description='正文出现的称呼变化。')
    custom_fields: "CollectionPatch[CustomField]" = Field(
        default_factory=lambda: CollectionPatch[CustomField]()
    , description='本幕涉及的其他自定义资料变化。')
    new_experiences: list[Experience] = Field(default_factory=list, description='角色在本幕实际经历的事件。')
    reason: str = Field(default="", description='变化对应的正文依据。')



class Extraction(Model):
    """依据最终确认正文提取的角色变化；不把规划视为事实。"""
    updates: list[StateUpdate] = Field(description='依据最终正文提取的逐角色变化。')
    decision_summary: str = Field(default="", description="可选的事实提取说明；遗漏不影响角色变化数据的校验")


class RewriteProposal(Model):
    summary: str = ""
    updates: list[StateUpdate] = Field(default_factory=list)
    materials: list[EditFieldsRequest] = Field(default_factory=list)
    source_revision_id: UUID
    scene_id: UUID | None = None
    chapter_id: UUID | None = None
    start: int
    end: int
    original: str
    replacement: str
    status: Literal["proposed", "accepted", "rejected"] = "proposed"


class ChatResult(Model):
    """不修改正文的角色访谈结果。"""
    session_id: UUID = Field(description="原样返回输入中的会话标识。")
    snapshot_id: UUID = Field(description="原样返回本次访谈所用的人物阶段快照标识。")
    reply: str = Field(description="符合该人物阶段知识和说话方式的回答。")
    canon: Literal[False] = Field(default=False, description="访谈不属于正式正文事实，恒为假。")


class CommandOutput(Model):
    discussion: DiscussionResult | None = None
    discussion_session: DiscussionSession | None = None
    revision_id: UUID | None = None
    story_id: UUID | None = None
    interview: InterviewResult | None = None
    rewrite: RewriteProposal | None = None
    chat: ChatResult | None = None
    need: CharacterNeed | None = None
    character: CharacterSpec | None = None
    approval_phase: Literal["consent", "profile"] | None = None
    message: str = ""
    story_deleted: bool = False
    story_restored: bool = False
    undone_command_id: UUID | None = None
    chat_session_id: UUID | None = None
    draft_id: UUID | None = None
    undone_command_ids: list[UUID] = Field(default_factory=list)
    writing_draft: WritingDraft | None = None
    prompt_template: PromptTemplateVersion | None = None
    field_template: FieldTemplate | None = None
    field_template_set: FieldTemplateSet | None = None
    recovered_command_id: UUID | None = None
    recovery_decision: Literal["retry", "withdraw"] | None = None


class ContextChunk(Model):
    id: UUID = Field(default_factory=uuid4)
    chapter_id: UUID
    scene_id: UUID | None = None
    ordinal: int
    kind: Literal["paragraph", "chapter"]
    content: str
    previous_context: str = ""
    next_context: str = ""


class NarrativeContext(Model):
    recent_scenes: list[Scene] = Field(default_factory=list)
    recalled: list[ContextChunk] = Field(default_factory=list)
    directions: list[Direction] = Field(default_factory=list)


class WritingState(Model):
    source_revision_id: UUID | None = None
    agent_results: list[dict[str, JsonValue]] = Field(default_factory=list)
    proposed_material_changes: list["EditFieldsRequest"] = Field(default_factory=list)
    planned_scene_id: UUID | None = None
    text_only_revision: bool = False
    command_id: UUID
    story: Story
    instruction: str
    new_chapter: bool = False
    intent: Intent | None = None
    context: NarrativeContext = Field(default_factory=NarrativeContext)
    plan: Plan | None = None
    consultations: list[Consultation] = Field(default_factory=list)
    approved_character: CharacterSpec | None = None
    rejected_character: bool = False
    approval_phase: Literal["consent", "profile"] | None = None
    draft: Draft | None = None
    judge: JudgeResult | None = None
    rewrites: int = 0
    extraction: Extraction | None = None
    scene_id: UUID = Field(default_factory=uuid4)
    maintenance: StoryMaintenance | None = None
    maintenance_checked: bool = False
    stage_maintenance_due: bool | None = None
    approval_response: ApprovalResponse | None = None
    execution_command_id: UUID | None = None
    draft_version: int = 0
    draft_status: Literal["writing", "pending", "confirmed", "discarded"] = "writing"
    review_response: DraftReviewResponse | None = None
    feedback: DraftReviewResponse | None = None
    revision_plan: RevisionPlan | None = None
    prompt_templates: list[PromptTemplateVersion] = Field(default_factory=list, max_length=15)
    edit_validation_feedback: str = ""
    rejected_edits: list[TextEdit] = Field(default_factory=list)

    @property
    def execution_id(self) -> UUID:
        return self.execution_command_id or self.command_id


class Usage(Model):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    estimated: bool = False


class Trace(Model):
    node: str
    summary: str
    prompt_version: str = "v1"
    model: str = ""
    duration_ms: int = 0
    usage: Usage = Field(default_factory=Usage)
    structured_input: str = ""
    structured_output: str = ""
    raw_output: str = ""
    reasoning_content: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    finish_reason: str | None = None
    attempt: int = Field(default=1, ge=1)
    error: str | None = None


class Event(Model):
    command_id: UUID
    actor: Literal["user", "ai", "system"] = "system"
    type: str
    trace: Trace | None = None
    output: CommandOutput | None = None
    created_at: datetime = Field(default_factory=now)


class CommandView(Model):
    submission: CommandSubmission
    status: Literal["queued", "running", "waiting", "completed", "failed"] = "queued"
    output: CommandOutput = Field(default_factory=CommandOutput)
    error: str | None = None
    created_at: datetime = Field(default_factory=now)
    undone: bool = False
    scene_id: UUID | None = None


class CommandLifecycle(Model):
    id: UUID
    status: Literal["queued", "running", "waiting", "completed", "failed"]
    undone_at: datetime | None = None


class CommandProjection(Model):
    id: UUID
    status: Literal["queued", "running", "waiting", "completed", "failed"]
    submission: CommandSubmission
    output: CommandOutput
    error: str | None = None
    created_at: datetime
    undone_at: datetime | None = None
    checkpoint_json: str | None = None


class CommandMemento(Model):
    command_id: UUID
    status: Literal["queued", "running", "waiting", "completed", "failed"]
    output: CommandOutput
    checkpoint: WritingState | None = None
    checkpoint_raw: str | None = None
    error: str | None = None
    undone_at: datetime | None = None


class OperationMemento(Model):
    draft_id: UUID | None = None
    draft_deleted: bool = False
    story_id: UUID | None = None
    story_existed: bool = False
    head_id: UUID | None = None
    deleted_at: datetime | None = None
    title: str | None = None
    commands: list[CommandMemento] = Field(default_factory=list)
    prompt_template_bindings: list["PromptTemplateBindingMemento"] = Field(default_factory=list)


class PromptTemplateBindingMemento(Model):
    scope_id: UUID
    task: PromptTask
    version_id: UUID | None = None


class UndoState(Model):
    operation_id: int | None = None
    command_id: UUID | None = None
    kind: str = ""
    available: bool = False
    message: str = ""
    command_ids: list[UUID] = Field(default_factory=list)


class CharacterPresentation(Model):
    character_id: UUID
    scene_id: UUID
    name: str
    aliases: list["Alias"]
    profile: Profile
    state: CharacterState
    custom_fields: list[CustomField] = Field(default_factory=list)


class StoryView(Model):
    revision_id: UUID
    story: Story

    @computed_field
    @property
    def characters_at_scenes(self) -> list[CharacterPresentation]:
        result: list[CharacterPresentation] = []
        visible: set[UUID] = set()
        for scene in self.story.scenes:
            visible.add(scene.id)
            for character in self.story.characters:
                if character.introduced_scene_id and character.introduced_scene_id not in visible:
                    continue
                snapshot = self.story.snapshot(character.id, scene.id)
                result.append(
                    CharacterPresentation(
                        character_id=character.id,
                        scene_id=scene.id,
                        name=character.name,
                        aliases=snapshot.aliases,
                        profile=snapshot.profile,
                        state=snapshot.state,
                        custom_fields=snapshot.custom_fields,
                    )
                )
        return result


class StorySummary(Model):
    id: UUID
    title: str
    revision_id: UUID
    scene_count: int
    deleted_at: datetime | None = None


class EventView(Model):
    id: int
    event: Event


class Health(Model):
    database: bool
    redis: bool
    worker: bool
    provider: str
    model: str = ""
    embedding_provider: str = ""
