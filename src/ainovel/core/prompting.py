"""Sandboxed, typed Jinja rendering for immutable AI prompt sources."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from uuid import NAMESPACE_URL, UUID, uuid5

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel

from ainovel.core.base import Model
from ainovel.core.discussion import DiscussionContext
from ainovel.core.domain import (
    PromptContextVariable,
    PromptTask,
    PromptTemplatePreviewContext,
    PromptTemplateVersion,
    WorkflowState,
)


class PromptRenderError(ValueError):
    """A prompt source could not be safely or completely rendered."""


class PromptMetadata(Model):
    task: PromptTask
    label: str
    description: str
    template_path: str


class PromptRuntimeContext(Model):
    """Only explicitly projected values may be referenced by prompt source."""

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

    @classmethod
    def from_input(cls, schema_name: str, context: BaseModel) -> PromptRuntimeContext:
        result = cls(schema_name=schema_name)
        if isinstance(context, WorkflowState):
            result.story_title = context.story.title
            result.instruction = context.instruction
            result.feedback = context.feedback.feedback if context.feedback else ""
            result.selected_text = (
                context.feedback.selection.selected_text
                if context.feedback and context.feedback.selection
                else ""
            )
            result.draft_text = context.draft.text if context.draft else ""
            result.draft_summary = context.draft.summary if context.draft else ""
            result.first_creation = not context.story.scenes
            result.quick_path = context.intent is not None and context.intent.route == "fast"
            result.maintenance_due = bool(context.stage_maintenance_due)
            if context.plan is not None:
                result.current_scene_summary = context.plan.scene_plan
            elif context.story.scenes:
                result.current_scene_summary = context.story.scenes[-1].summary
        elif isinstance(context, DiscussionContext):
            result.story_title = context.story.title
            result.discussion_message = context.message
            result.conversation_history = "\n".join(
                f"{turn.role}: {turn.content}" for turn in context.history
            )
            result.tool_results = "\n".join(item.message for item in context.tool_results)
            result.current_scene_summary = "\n".join(item.summary for item in context.story.recent_scenes)
        else:
            # These are typed request/brief models. Copy only the explicit scalar fields
            # the prompt catalog supports; never expose Settings, ports or arbitrary objects.
            for field in (
                "story_title",
                "title",
                "instruction",
                "current_scene_summary",
                "situation",
                "feedback",
                "selected_text",
                "selection",
                "draft_text",
                "draft_summary",
                "character_name",
                "name",
                "role_profile",
                "profile",
                "discussion_message",
                "message",
                "original",
            ):
                value = getattr(context, field, None)
                if isinstance(value, str):
                    target = {
                        "title": "story_title",
                        "situation": "current_scene_summary",
                        "selection": "selected_text",
                        "name": "character_name",
                        "profile": "role_profile",
                        "message": "discussion_message",
                        "original": "selected_text",
                    }.get(field, field)
                    setattr(result, target, value)
                elif isinstance(value, BaseModel) and field == "feedback":
                    setattr(result, "feedback", getattr(value, "feedback", ""))
                elif isinstance(value, BaseModel) and field == "profile":
                    setattr(
                        result,
                        "role_profile",
                        value.model_dump_json(exclude_none=True, exclude_defaults=True),
                    )
            if hasattr(context, "turns"):
                turns = getattr(context, "turns")
                result.conversation_history = "\n".join(f"{item.role}: {item.content}" for item in turns)
            if hasattr(context, "history") and not result.conversation_history:
                history = getattr(context, "history")
                result.conversation_history = "\n".join(f"{item.role}: {item.content}" for item in history)
            result.first_creation = bool(getattr(context, "first_scene", False))
            result.quick_path = bool(getattr(context, "quick_path", False))
            result.maintenance_due = bool(getattr(context, "maintenance_due", False))
        return result


class RenderedPrompt(Model):
    text: str
    template_path: str
    source_hash: str
    context_variables: list[str]
    context_schema: list[PromptContextVariable]


PROMPT_METADATA: tuple[PromptMetadata, ...] = (
    PromptMetadata(
        task="planning_decision",
        label="意图判断与路线规划",
        description="判断目标、路线、召回与角色访谈。",
        template_path="prompts/planning_decision.j2",
    ),
    PromptMetadata(
        task="planning",
        label="场景规划",
        description="规划本章和场景，并维护后续计划。",
        template_path="prompts/planning.j2",
    ),
    PromptMetadata(
        task="revision_plan",
        label="草稿重写规划",
        description="分析修改范围、相关角色和重写策略。",
        template_path="prompts/revision_plan.j2",
    ),
    PromptMetadata(
        task="character_consultation",
        label="角色访谈",
        description="按当前阶段角色的知识与意图回应情境。",
        template_path="prompts/character_consultation.j2",
    ),
    PromptMetadata(
        task="rewrite_selection",
        label="选区改写",
        description="只改写指定文本，并保留其他正文。",
        template_path="prompts/rewrite_selection.j2",
    ),
    PromptMetadata(
        task="writing",
        label="正文生成",
        description="依据要求、资料和规划写作正文。",
        template_path="prompts/writing.j2",
    ),
    PromptMetadata(
        task="judge",
        label="草稿审阅",
        description="检查要求、连续性、人物和文风。",
        template_path="prompts/judge.j2",
    ),
    PromptMetadata(
        task="extraction",
        label="事实与记忆提取",
        description="从最终正文提取事实、角色状态与经历。",
        template_path="prompts/extraction.j2",
    ),
    PromptMetadata(
        task="maintenance",
        label="长期资料维护",
        description="维护世界设定、偏好、剧情计划和笔记。",
        template_path="prompts/maintenance.j2",
    ),
    PromptMetadata(
        task="creation_interview",
        label="新故事构思访谈",
        description="通过对话整理世界观、角色和写作偏好。",
        template_path="prompts/creation_interview.j2",
    ),
    PromptMetadata(
        task="rewrite_snippet",
        label="正文选区改写",
        description="依据备注改写已选中的正文片段。",
        template_path="prompts/rewrite_snippet.j2",
    ),
    PromptMetadata(
        task="scene_summary",
        label="正文事实摘要",
        description="简短概括正文中实际发生的事件。",
        template_path="prompts/scene_summary.j2",
    ),
    PromptMetadata(
        task="character_profile",
        label="新角色档案",
        description="创建待用户检查的新角色档案。",
        template_path="prompts/character_profile.j2",
    ),
    PromptMetadata(
        task="character_chat",
        label="角色采访",
        description="扮演当前阶段角色进行非正文交流。",
        template_path="prompts/character_chat.j2",
    ),
    PromptMetadata(
        task="discussion",
        label="故事讨论",
        description="讨论故事资料并按明确授权保存变更。",
        template_path="prompts/discussion.j2",
    ),
)
PROMPT_CONTEXT_VARIABLES = list(PromptRuntimeContext.model_fields)
_VARIABLES = {
    "schema_name": PromptContextVariable(
        name="schema_name", type="string", description="本次结构化结果类型。"
    ),
    "story_title": PromptContextVariable(name="story_title", type="string", description="当前作品标题。"),
    "instruction": PromptContextVariable(name="instruction", type="string", description="用户本次创作要求。"),
    "current_scene_summary": PromptContextVariable(
        name="current_scene_summary", type="string", description="近期场景或当前情境摘要。"
    ),
    "feedback": PromptContextVariable(name="feedback", type="string", description="用户对待修改草稿的意见。"),
    "selected_text": PromptContextVariable(
        name="selected_text", type="string", description="用户选中的原文片段。"
    ),
    "draft_text": PromptContextVariable(name="draft_text", type="string", description="当前候选草稿正文。"),
    "draft_summary": PromptContextVariable(name="draft_summary", type="string", description="当前草稿摘要。"),
    "character_name": PromptContextVariable(
        name="character_name", type="string", description="本次相关角色的名称。"
    ),
    "role_profile": PromptContextVariable(
        name="role_profile", type="string", description="本次角色阶段档案的文本投影。"
    ),
    "discussion_message": PromptContextVariable(
        name="discussion_message", type="string", description="故事讨论中的当前用户消息。"
    ),
    "conversation_history": PromptContextVariable(
        name="conversation_history", type="string", description="已提供的会话历史文本。"
    ),
    "tool_results": PromptContextVariable(
        name="tool_results", type="string", description="本轮已执行工具返回的摘要文本。"
    ),
    "first_creation": PromptContextVariable(
        name="first_creation", type="boolean", description="是否为首次创作。"
    ),
    "quick_path": PromptContextVariable(
        name="quick_path", type="boolean", description="是否采用快速创作路线。"
    ),
    "maintenance_due": PromptContextVariable(
        name="maintenance_due", type="boolean", description="是否到达长期资料整理周期。"
    ),
}
_TASK_VARIABLES: dict[PromptTask, tuple[str, ...]] = {
    "planning_decision": (
        "schema_name",
        "story_title",
        "instruction",
        "current_scene_summary",
        "feedback",
        "first_creation",
    ),
    "planning": (
        "schema_name",
        "story_title",
        "instruction",
        "current_scene_summary",
        "first_creation",
        "quick_path",
    ),
    "revision_plan": ("schema_name", "story_title", "instruction", "feedback", "selected_text", "draft_text"),
    "character_consultation": (
        "schema_name",
        "story_title",
        "instruction",
        "current_scene_summary",
        "character_name",
        "role_profile",
    ),
    "rewrite_selection": ("schema_name", "instruction", "selected_text", "draft_text", "feedback"),
    "writing": (
        "schema_name",
        "story_title",
        "instruction",
        "current_scene_summary",
        "feedback",
        "selected_text",
        "draft_text",
        "draft_summary",
    ),
    "judge": ("schema_name", "instruction", "feedback", "draft_text", "draft_summary"),
    "extraction": (
        "schema_name",
        "story_title",
        "instruction",
        "draft_text",
        "draft_summary",
        "maintenance_due",
    ),
    "maintenance": (
        "schema_name",
        "story_title",
        "instruction",
        "draft_text",
        "draft_summary",
        "maintenance_due",
    ),
    "creation_interview": ("schema_name", "conversation_history", "discussion_message"),
    "rewrite_snippet": ("schema_name", "instruction", "current_scene_summary", "selected_text"),
    "scene_summary": ("schema_name", "draft_text", "current_scene_summary"),
    "character_profile": ("schema_name", "character_name", "role_profile"),
    "character_chat": (
        "schema_name",
        "story_title",
        "character_name",
        "role_profile",
        "conversation_history",
        "discussion_message",
    ),
    "discussion": (
        "schema_name",
        "story_title",
        "current_scene_summary",
        "discussion_message",
        "conversation_history",
        "tool_results",
    ),
}
_METADATA_BY_TASK = {item.task: item for item in PROMPT_METADATA}
_ENVIRONMENT = SandboxedEnvironment(
    undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True
)


def prompt_metadata(task: PromptTask) -> PromptMetadata:
    return _METADATA_BY_TASK[task]


def prompt_context_schema(task: PromptTask) -> list[PromptContextVariable]:
    return [_VARIABLES[name] for name in _TASK_VARIABLES[task]]


def default_prompt_source(task: PromptTask) -> str:
    metadata = prompt_metadata(task)
    resource = files("ainovel").joinpath(*metadata.template_path.split("/"))
    try:
        return resource.read_text(encoding="utf-8")
    except OSError as error:
        raise PromptRenderError(f"缺少提示词资源 {metadata.template_path}: {error}") from error


def prompt_source_hash(source: str, legacy_guidance: str = "") -> str:
    payload = source + (
        "\n\n【旧版提示词补充】\n" + legacy_guidance.strip() if legacy_guidance.strip() else ""
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_prompt_source(
    task: PromptTask,
    source: str,
    context: PromptRuntimeContext,
    *,
    legacy_guidance: str = "",
    template_path: str | None = None,
) -> RenderedPrompt:
    if len(source) > 25000:
        raise PromptRenderError("提示词模板不得超过 25000 字符")
    try:
        rendered = _ENVIRONMENT.from_string(source).render(**context.model_dump())
    except TemplateError as error:
        raise PromptRenderError(f"Jinja 提示词无法渲染：{error}") from error
    if legacy_guidance.strip():
        rendered += "\n\n【旧版创作策略补充】\n" + legacy_guidance.strip()
    return RenderedPrompt(
        text=rendered,
        template_path=template_path or prompt_metadata(task).template_path,
        source_hash=prompt_source_hash(source, legacy_guidance),
        context_variables=[item.name for item in prompt_context_schema(task)],
        context_schema=prompt_context_schema(task),
    )


def render_default_prompt(task: PromptTask, context: PromptRuntimeContext) -> RenderedPrompt:
    source = default_prompt_source(task)
    return render_prompt_source(task, source, context)


def preview_prompt_source(
    task: PromptTask,
    source: str | None,
    preview_context: PromptTemplatePreviewContext,
    *,
    legacy_guidance: str = "",
) -> RenderedPrompt:
    selected_source = source if source is not None else default_prompt_source(task)
    context = PromptRuntimeContext.model_validate(preview_context.model_dump())
    return render_prompt_source(task, selected_source, context, legacy_guidance=legacy_guidance)


def prompt_context_for(schema_name: str, context: BaseModel) -> PromptRuntimeContext:
    return PromptRuntimeContext.from_input(schema_name, context)


def prompt_version_label(template: PromptTemplateVersion) -> str:
    source, legacy_guidance = prompt_source_for(template)
    digest = prompt_source_hash(source, legacy_guidance)
    path = template.template_path or prompt_metadata(template.task).template_path
    return f"{template.task}:v{template.version}:{template.id}:{digest[:12]}:{path}"


def workflow_template(state: WorkflowState, task: PromptTask) -> PromptTemplateVersion | None:
    return next((item for item in state.prompt_templates if item.task == task), None)


def prompt_source_for(template: PromptTemplateVersion) -> tuple[str, str]:
    if template.source:
        return template.source, template.guidance if template.source_format == "legacy_guidance" else ""
    return default_prompt_source(template.task), template.guidance


GLOBAL_PROMPT_SCOPE_ID = UUID("00000000-0000-0000-0000-000000000111")


def default_template(task: PromptTask) -> PromptTemplateVersion:
    metadata = prompt_metadata(task)
    source = default_prompt_source(task)
    source_hash = prompt_source_hash(source)
    return PromptTemplateVersion(
        id=uuid5(NAMESPACE_URL, f"ainovel:prompt-template:{task}:default:v2:{source_hash}"),
        scope="global",
        scope_id=GLOBAL_PROMPT_SCOPE_ID,
        task=task,
        name=f"默认：{metadata.label}",
        version=2,
        guidance="",
        source_format="jinja2",
        source=source,
        editor_source=source,
        template_path=metadata.template_path,
        source_hash=source_hash,
        context_variables=[item.name for item in prompt_context_schema(task)],
        context_schema=prompt_context_schema(task),
        builtin=True,
    )
