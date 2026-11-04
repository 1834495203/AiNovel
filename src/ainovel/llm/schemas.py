"""Validated provider wire schemas and compact prompt contexts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, RootModel

from ainovel.core.characters import Alias, CharacterSpec, CharacterState, Profile
from ainovel.core.domain import (
    Consultation,
    ContextChunk,
    Direction,
    Draft,
    DraftReviewResponse,
    Experience,
    Extraction,
    Intent,
    JudgeResult,
    Model,
    Plan,
    RevisionPlan,
    StoryMaintenance,
    WritingState,
)
from ainovel.core.fields import FieldDefinition
from ainovel.core.records import (
    CustomField,
    Entry,
    Foreshadow,
    OutlineBeat,
    PlotThread,
    Preference,
    ReaderPreferences,
    WorldSetting,
    WriterNote,
    WriterPatch,
    WriterWorkspace,
)
from ainovel.core.text_edits import TextEdit


class Message(Model):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list["ToolCall"] | None = None


class ToolParameters(RootModel[JsonValue]):
    """Provider JSON Schema boundary; generated only from a Pydantic result model."""


class FunctionDefinition(Model):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str
    parameters: ToolParameters


class ToolDefinition(Model):
    type: Literal["function"] = "function"
    function: FunctionDefinition


class FunctionChoice(Model):
    name: str


class ToolChoice(Model):
    type: Literal["function"] = "function"
    function: FunctionChoice


class ThinkingMode(Model):
    type: Literal["enabled", "disabled"]


class ChatBody(Model):
    model: str
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int = Field(default=8192, ge=1)
    tools: list[ToolDefinition] | None = None
    tool_choice: ToolChoice | Literal["auto"] | None = None


class DeepSeekChatBody(ChatBody):
    thinking: ThinkingMode


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ProviderMessage(BaseModel):
    role: str
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


Message.model_rebuild()


class Choice(BaseModel):
    # Providers commonly add index, finish_reason, logprobs, refusal and annotations.
    message: ProviderMessage
    finish_reason: str | None = None


class TokenDetails(BaseModel):
    cached_tokens: int = 0


class ProviderUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_tokens_details: TokenDetails | None = None
    prompt_cache_hit_tokens: int | None = None


class ChatResponse(BaseModel):
    choices: list[Choice]
    usage: ProviderUsage = Field(default_factory=ProviderUsage)


class EmbeddingBody(Model):
    model: str
    input: list[str]
    dimensions: int


class EmbeddingItem(BaseModel):
    index: int
    embedding: list[float]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int | None = None
    total_tokens: int | None = None


class EmbeddingResponse(BaseModel):
    data: list[EmbeddingItem]
    usage: EmbeddingUsage = Field(default_factory=EmbeddingUsage)


class TextResult(Model):
    text: str = Field(description='本次请求的文本结果。')


class CharacterBrief(Model):
    id: UUID
    name: str
    aliases: list[Alias]
    profile: Profile
    state: CharacterState


class RecentSceneBrief(Model):
    id: UUID
    chapter_id: UUID
    title: str
    text: str
    summary: str


class PromptContext(Model):
    recent_scenes: list[RecentSceneBrief]
    recalled: list[ContextChunk]
    directions: list[Direction]


class ChapterBrief(Model):
    id: UUID
    title: str
    summary: str
    scene_count: int


class NarrativeInput(Model):
    """Bounded model context; never send the entire novel aggregate to every graph node."""

    story_id: UUID
    title: str
    world: WorldSetting
    preferences: ReaderPreferences
    writer: WriterWorkspace
    custom_fields: list[CustomField]
    characters: list[CharacterBrief]
    scene_id: UUID
    instruction: str
    intent: Intent | None
    context: PromptContext
    plan: Plan | None
    consultations: list[Consultation]
    approved_character: CharacterSpec | None
    rejected_character: bool
    draft: Draft | None
    judge: JudgeResult | None
    chapters: list[ChapterBrief]
    feedback: DraftReviewResponse | None
    revision_plan: RevisionPlan | None

    @classmethod
    def from_state(cls, state: WritingState) -> "NarrativeInput":
        writer = (
            state.plan.writer_patch.apply(state.story.writer)
            if state.plan
            else state.story.writer.model_copy(deep=True)
        )
        writer.notes = writer.notes[-24:]
        drafts = writer.drafts[-2:]
        for draft in drafts:
            draft.content = draft.content[:1500]
        writer.drafts = drafts
        return cls(
            feedback=state.feedback,
            revision_plan=state.revision_plan,
            story_id=state.story.id,
            title=state.story.title,
            world=state.story.world,
            preferences=state.story.preferences,
            writer=writer,
            custom_fields=state.story.custom_fields,
            characters=[
                CharacterBrief(
                    id=character.id,
                    name=character.name,
                    aliases=state.story.snapshot(character.id).aliases,
                    profile=state.story.snapshot(character.id).profile,
                    state=state.story.snapshot(character.id).state,
                )
                for character in state.story.characters
            ],
            scene_id=state.scene_id,
            instruction=state.instruction,
            intent=state.intent,
            context=PromptContext(
                recent_scenes=[
                    RecentSceneBrief(
                        id=scene.id,
                        chapter_id=scene.chapter_id,
                        title=scene.title,
                        text=scene.text,
                        summary=scene.summary,
                    )
                    for scene in state.context.recent_scenes
                ],
                recalled=state.context.recalled,
                directions=state.context.directions,
            ),
            plan=state.plan,
            consultations=state.consultations,
            approved_character=state.approved_character,
            rejected_character=state.rejected_character,
            draft=state.draft,
            judge=state.judge,
            chapters=[
                ChapterBrief(
                    id=chapter.id,
                    title=chapter.title,
                    summary=chapter.summary,
                    scene_count=sum(scene.chapter_id == chapter.id for scene in state.story.scenes),
                )
                for chapter in state.story.chapters[-2:]
            ],
        )


class PlanningDecision(Model):
    """Combines route selection and scene planning into one model call."""

    intent: Intent
    plan: Plan


class FinalizationResult(Model):
    """Confirmed-scene facts plus an optional stage-level maintenance patch."""

    extraction: Extraction = Field(description='最终正文中的角色变化。')
    maintenance: StoryMaintenance | None = Field(default=None, description='需要更新的已有故事资料；无变化保持空补丁。')


class DraftRevisionResult(Model):
    edits: list[TextEdit] = Field(
        description="实际需要的修改列表；所有原文片段必须来自同一份修改前草稿且互不重叠。无需修改时为空。上一份结果校验失败时，它尚未应用，必须依据原草稿重新提交完整列表，不能只补交修正的一项。"
    )
    summary: str = Field(description="应用这些修改后整幕正文的简短事实摘要")


class RewriteSelectionResult(Model):
    replacement: str = Field(min_length=1, description='替换用户选区的完整新文本。')
    summary: str = Field(description='改写后本幕实际发生事件的摘要。')


class EntryPromptBrief(Model):
    id: UUID
    name: str
    value: str | int | float | bool | None = None
    description: str = ""
    children: list["EntryPromptBrief"] = Field(default_factory=list)
    definition: "FieldDefinition | None" = None


class RecordPromptBrief(Model):
    id: UUID
    name: str
    description: str = ""
    status: str = ""
    progress: str = ""
    next_steps: list[str] = Field(default_factory=list)


class PreferencePromptBrief(RecordPromptBrief):
    origin: Literal["explicit", "inferred"] = "explicit"
    confidence: float = 1
    custom_fields: list[EntryPromptBrief] = Field(default_factory=list)


class MaterialFieldsBrief(Model):
    scope: str
    entity_id: UUID
    entries: list[EntryPromptBrief]


class AliasPromptBrief(Model):
    id: UUID
    name: str
    reason: str = ""
    source_scene_id: UUID | None = None


class WriterPromptBrief(Model):
    mainlines: list[PlotThread] = Field(default_factory=list)
    subplots: list[PlotThread] = Field(default_factory=list)
    foreshadows: list[Foreshadow] = Field(default_factory=list)
    outline: list[OutlineBeat] = Field(default_factory=list)
    future_directions: list[PlotThread] = Field(default_factory=list)
    notes: list[WriterNote] = Field(default_factory=list)
    custom_fields: list[EntryPromptBrief] = Field(default_factory=list)
    progress: str = ""
    current_user_intent: str = ""


class CharacterPromptBrief(Model):
    id: UUID
    name: str
    aliases: list[AliasPromptBrief] = Field(default_factory=list)
    profile: list[EntryPromptBrief] = Field(default_factory=list)
    state: list[EntryPromptBrief] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    custom_fields: list[EntryPromptBrief] = Field(default_factory=list)


class AnalysisInput(Model):
    """Small context for route selection and planning; excludes prose and snapshots."""

    title: str
    instruction: str
    first_scene: bool = False
    world_premise: str = ""
    world: list[EntryPromptBrief] = Field(default_factory=list)
    likes: list[PreferencePromptBrief] = Field(default_factory=list)
    dislikes: list[PreferencePromptBrief] = Field(default_factory=list)
    progress: str = ""
    writer: WriterPromptBrief
    chapters: list[str] = Field(default_factory=list)
    characters: list[CharacterPromptBrief] = Field(default_factory=list)
    recent_summaries: list[str] = Field(default_factory=list)
    feedback: str = ""
    material_fields: list[MaterialFieldsBrief] = Field(default_factory=list)


class WritingInput(Model):
    """Scene-level facts only; no full novel history or unrelated writer records."""

    title: str
    scene_id: UUID
    instruction: str
    intent: Intent | None = None
    plan: Plan | None = None
    world_premise: str = ""
    world: list[EntryPromptBrief] = Field(default_factory=list)
    writer: WriterPromptBrief
    likes: list[PreferencePromptBrief] = Field(default_factory=list)
    dislikes: list[PreferencePromptBrief] = Field(default_factory=list)
    characters: list[CharacterPromptBrief] = Field(default_factory=list)
    preceding_scene: RecentSceneBrief | None = None
    recalled: list[ContextChunk] = Field(default_factory=list)
    directions: list[Direction] = Field(default_factory=list)
    consultations: list[Consultation] = Field(default_factory=list)
    approved_character: CharacterSpec | None = None
    rejected_character: bool = False
    draft: Draft | None = None
    feedback: DraftReviewResponse | None = None
    revision_plan: RevisionPlan | None = None
    edit_validation_feedback: str = Field(
        default="",
        description="上一份修改列表的校验错误。非空时修复这些错误，重新提交完整、不重叠的 edits；所有原文仍来自当前未被修改的 draft.text",
    )
    rejected_edits: list[TextEdit] = Field(
        default_factory=list,
        description="上一份未应用的修改列表，错误信息的修改编号对应本列表；修复时重新提交完整列表",
    )
    material_fields: list[MaterialFieldsBrief] = Field(default_factory=list)


class ReplanningInput(Model):
    original_context: WritingInput
    revision_plan: RevisionPlan | None = None


class JudgeInput(Model):
    instruction: str
    constraints: list[str] = Field(default_factory=list)
    draft: Draft
    world_premise: str = ""
    directions: list[Direction] = Field(default_factory=list)
    characters: list[CharacterPromptBrief] = Field(default_factory=list)
    material_fields: list[MaterialFieldsBrief] = Field(default_factory=list)


class RewritePlanInput(Model):
    original_context: WritingInput
    instruction: str
    feedback: DraftReviewResponse
    draft: Draft
    selected_text: str = ""
    characters: list[CharacterPromptBrief] = Field(default_factory=list)


class FinalizationInput(Model):
    scene_id: UUID
    draft: Draft
    characters: list[CharacterPromptBrief] = Field(default_factory=list)
    instruction: str = ""
    world_premise: str = ""
    world: list[EntryPromptBrief] = Field(default_factory=list)
    likes: list[PreferencePromptBrief] = Field(default_factory=list)
    dislikes: list[PreferencePromptBrief] = Field(default_factory=list)
    writer: WriterPromptBrief
    writer_patch: WriterPatch | None = None
    maintenance_due: bool = False
    material_fields: list[MaterialFieldsBrief] = Field(default_factory=list)


def project_context(schema: type[Model], state: WritingState, maintenance_interval: int = 5) -> Model:
    """Build a node-specific context instead of serializing WritingState wholesale."""

    def entry_brief(entry: Entry) -> EntryPromptBrief:
        return EntryPromptBrief(
            id=entry.id,
            name=entry.name,
            value=entry.value,
            description=entry.description,
            children=[entry_brief(child) for child in entry.children],
            definition=entry.definition,
        )

    def preference_brief(item: Preference) -> PreferencePromptBrief:
        return PreferencePromptBrief(
            id=item.id,
            name=item.name,
            description=item.description,
            status="active" if item.active else "inactive",
            origin=item.origin,
            confidence=item.confidence,
            custom_fields=[entry_brief(entry) for entry in item.custom_fields],
        )

    def writer_prompt(*, stage: bool = False) -> WriterPromptBrief:
        writer = state.story.writer

        def relevant[T](items: list[T]) -> list[T]:
            if stage:
                return items
            return [
                item
                for item in items
                if getattr(item, "status", "active")
                not in ("resolved", "abandoned", "completed", "dropped", "paid_off")
            ]

        return WriterPromptBrief(
            mainlines=relevant(writer.mainlines),
            subplots=relevant(writer.subplots),
            foreshadows=relevant(writer.foreshadows),
            outline=relevant(writer.outline),
            future_directions=relevant(writer.future_directions),
            notes=writer.notes if stage else [],
            custom_fields=[entry_brief(entry) for entry in writer.custom_fields],
            progress=writer.progress,
            current_user_intent=writer.current_user_intent,
        )

    def alias_briefs(aliases: list[Alias]) -> list[AliasPromptBrief]:
        return [
            AliasPromptBrief(
                id=alias.id,
                name=alias.name,
                reason=alias.reason,
                source_scene_id=alias.source_scene_id,
            )
            for alias in aliases
        ]

    def characters(
        names: list[str] | None = None, mentioned_only: bool = False
    ) -> list[CharacterPromptBrief]:
        result: list[CharacterPromptBrief] = []
        for character in state.story.characters:
            snapshot = state.story.snapshot(character.id)
            aliases = [item.name for item in snapshot.aliases]
            if names is not None and character.name not in names:
                continue
            if mentioned_only and not any(name in state.draft.text for name in [character.name, *aliases]):
                continue
            result.append(
                CharacterPromptBrief(
                    id=character.id,
                    name=character.name,
                    aliases=alias_briefs(snapshot.aliases),
                    profile=[entry_brief(entry) for entry in snapshot.profile.entries],
                    state=[entry_brief(entry) for entry in snapshot.state.entries],
                    experiences=snapshot.experiences[-5:],
                    custom_fields=[entry_brief(entry) for entry in snapshot.custom_fields],
                )
            )
        return result

    from ainovel.core.field_templates import definition_id

    world = [
        entry_brief(entry)
        for entry in state.story.world.entries
        if not entry.definition or entry.definition.id != definition_id("world", "premise")
    ]
    likes = [preference_brief(item) for item in state.story.preferences.likes if item.active]
    dislikes = [preference_brief(item) for item in state.story.preferences.dislikes if item.active]
    writer = writer_prompt()
    due = maintenance_due(state, maintenance_interval)
    material_fields = [
        MaterialFieldsBrief(
            scope=scope, entity_id=identity, entries=[entry_brief(entry) for entry in entries]
        )
        for scope, identity, entries in [
            ("story", state.story.id, state.story.custom_fields),
            ("preferences", state.story.id, state.story.preferences.custom_fields),
            *[("chapter", item.id, item.custom_fields) for item in state.story.chapters[-1:]],
            *[("scene", item.id, item.custom_fields) for item in state.story.scenes[-1:]],
        ]
        if entries
    ]

    if schema is Plan and state.feedback:
        return ReplanningInput(
            original_context=project_context(Draft, state), revision_plan=state.revision_plan
        )
    if schema in (PlanningDecision, Plan):
        return AnalysisInput(
            material_fields=material_fields,
            title=state.story.title,
            instruction=state.instruction,
            first_scene=not state.story.scenes,
            world_premise=state.story.world.premise,
            world=world,
            likes=likes,
            dislikes=dislikes,
            progress=state.story.writer.progress,
            writer=writer,
            chapters=[f"{item.title}: {item.summary[:300]}" for item in state.story.chapters[-3:]],
            characters=[
                CharacterPromptBrief(
                    id=character.id,
                    name=character.name,
                    aliases=alias_briefs(state.story.snapshot(character.id).aliases),
                    profile=[
                        entry_brief(entry) for entry in state.story.snapshot(character.id).profile.entries
                    ],
                    state=[entry_brief(entry) for entry in state.story.snapshot(character.id).state.entries],
                    experiences=state.story.snapshot(character.id).experiences[-3:],
                    custom_fields=[
                        entry_brief(entry) for entry in state.story.snapshot(character.id).custom_fields
                    ],
                )
                for character in state.story.characters
            ],
            recent_summaries=[scene.summary[:500] for scene in state.story.scenes[-2:]],
            feedback=state.feedback.feedback[:1500] if state.feedback else "",
        )
    if schema is JudgeResult:
        if state.draft is None:
            raise ValueError("审阅缺少草稿")
        selected = state.plan.character_names if state.plan else []
        return JudgeInput(
            material_fields=material_fields,
            instruction=state.instruction,
            world_premise=state.story.world.premise,
            constraints=state.intent.constraints if state.intent else [],
            draft=state.draft,
            directions=state.context.directions,
            characters=characters(selected or None),
        )
    if schema is RevisionPlan:
        if state.draft is None or state.feedback is None:
            raise ValueError("修改规划缺少草稿或用户意见")
        selected = state.feedback.selection.selected_text if state.feedback.selection else ""
        return RewritePlanInput(
            original_context=WritingInput.model_validate(project_context(Draft, state)).model_copy(
                update={"draft": None, "feedback": None, "revision_plan": None}
            ),
            instruction=state.instruction,
            feedback=state.feedback,
            draft=state.draft,
            selected_text=selected,
            characters=characters(mentioned_only=True),
        )
    if schema in (Extraction, FinalizationResult):
        if state.draft is None:
            raise ValueError("提取缺少最终正文")
        return FinalizationInput(
            material_fields=material_fields,
            scene_id=state.scene_id,
            draft=state.draft,
            characters=characters(mentioned_only=True),
            instruction=state.instruction,
            world_premise=state.story.world.premise,
            likes=likes,
            dislikes=dislikes,
            writer_patch=state.plan.writer_patch if state.plan else None,
            world=world,
            writer=writer_prompt(stage=due),
            maintenance_due=due,
        )
    if schema is StoryMaintenance:
        if state.draft is None:
            raise ValueError("阶段整理缺少最终正文")
        return FinalizationInput(
            material_fields=material_fields,
            scene_id=state.scene_id,
            draft=state.draft,
            instruction=state.instruction,
            world_premise=state.story.world.premise,
            world=world,
            likes=likes,
            dislikes=dislikes,
            writer=writer_prompt(stage=True),
            maintenance_due=True,
        )
    if schema in (Draft, DraftRevisionResult):
        selected = state.plan.character_names if state.plan else []
        return WritingInput(
            material_fields=material_fields,
            title=state.story.title,
            scene_id=state.scene_id,
            instruction=state.instruction,
            intent=state.intent,
            plan=state.plan,
            world_premise=state.story.world.premise,
            world=world,
            writer=writer,
            likes=likes,
            dislikes=dislikes,
            characters=characters(selected or None),
            preceding_scene=(
                RecentSceneBrief(
                    id=state.context.recent_scenes[-1].id,
                    chapter_id=state.context.recent_scenes[-1].chapter_id,
                    title=state.context.recent_scenes[-1].title,
                    text=state.context.recent_scenes[-1].text,
                    summary=state.context.recent_scenes[-1].summary,
                )
                if state.context.recent_scenes
                else None
            ),
            recalled=state.context.recalled,
            directions=state.context.directions,
            consultations=state.consultations,
            approved_character=state.approved_character,
            rejected_character=state.rejected_character,
            draft=state.draft,
            feedback=state.feedback,
            revision_plan=state.revision_plan,
            edit_validation_feedback=state.edit_validation_feedback if schema is DraftRevisionResult else "",
            rejected_edits=state.rejected_edits if schema is DraftRevisionResult else [],
        )
    return NarrativeInput.from_state(state)


def maintenance_due(state: WritingState, interval: int = 5) -> bool:
    """True at story start, an explicit deep turn/chapter boundary, or fallback cadence."""

    if not state.story.scenes or (state.intent and state.intent.route == "deliberate") or state.new_chapter:
        return True
    return (len(state.story.scenes) + 1) % interval == 0
