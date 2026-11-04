"""Sandboxed, typed Jinja rendering for immutable AI prompt sources."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from uuid import NAMESPACE_URL, UUID, uuid5

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field

from ainovel.core.base import Model
from ainovel.core.context_layout import SECTIONS, default_context_source
from ainovel.core.discussion import DiscussionContext
from ainovel.core.domain import (
    PromptContextVariable,
    PromptTask,
    PromptTemplatePreviewContext,
    PromptTemplateVersion,
    WritingState,
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

    world_context: str = Field(default="", description="已填写的世界规则和资料，JSON 文本。")
    characters_context: str = Field(default="", description="相关人物档案和当前状态，JSON 文本。")
    preferences_context: str = Field(default="", description="用户已填写的写作偏好，JSON 文本。")
    writer_context: str = Field(default="", description="已有创作资料，不自动生成固定字段。")
    scene_plans_context: str = Field(default="", description="幕计划及路线 ID，JSON 文本。")
    recent_story_context: str = Field(default="", description="近期已确认正文或摘要，JSON 文本。")
    task_context: str = Field(default="", description="本次任务的请求、草稿或修改意见，JSON 文本。")
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
        if isinstance(context, WritingState):
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
            result.first_creation = not context.story.recent_scenes
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
    PromptMetadata(task="creation_interview",label="创建小说",description="交流构思并提交初始提案。",template_path="prompts/creation_interview.j2"),
    PromptMetadata(task="scene_planning",label="幕设定",description="通过工具批量创建与修改幕。",template_path="prompts/scene_planning.j2"),
    PromptMetadata(task="writing",label="正文写作",description="按既有幕计划写作或修改正文。",template_path="prompts/writing.j2"),
    PromptMetadata(task="discussion",label="普通讨论",description="查询资料与讨论想法，不修改小说。",template_path="prompts/discussion.j2"),
    PromptMetadata(task="character_chat",label="角色对话",description="以指定故事阶段的人物身份交流。",template_path="prompts/character_chat.j2"),
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
_VARIABLES.update({name: PromptContextVariable(name=name,type="string",description=description) for name,description in SECTIONS.items()})
_TASK_VARIABLES = {task: tuple(name for name in PROMPT_CONTEXT_VARIABLES if name not in SECTIONS) for task in ("creation_interview","scene_planning","writing","discussion","character_chat")}

_METADATA_BY_TASK = {item.task:item for item in PROMPT_METADATA}
_ENVIRONMENT = SandboxedEnvironment(undefined=StrictUndefined,autoescape=False,keep_trailing_newline=True)
_ENVIRONMENT.globals.clear()

def prompt_metadata(task: PromptTask) -> PromptMetadata:
    return _METADATA_BY_TASK[task]


def prompt_context_schema(task: PromptTask) -> list[PromptContextVariable]:
    names = _TASK_VARIABLES.get(task, _TASK_VARIABLES["discussion"])
    return [_VARIABLES[name] for name in (*names, *SECTIONS)]



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


def workflow_template(state: WritingState, task: PromptTask) -> PromptTemplateVersion | None:
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
        context_source=default_context_source(),
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
