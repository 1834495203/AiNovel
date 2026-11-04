from uuid import uuid4

import pytest

import ainovel.service.prompt_templates as prompt_service
from ainovel.core.domain import (
    PROMPT_TASKS,
    PromptTemplatePreviewContext,
    PromptTemplateSelection,
    PromptTemplateVersion,
)
from ainovel.core.prompting import (
    PromptRenderError,
    PromptRuntimeContext,
    default_prompt_source,
    preview_prompt_source,
    render_prompt_source,
)
from ainovel.service.prompt_templates import (
    GLOBAL_PROMPT_SCOPE_ID,
    _project_version,
    default_templates,
    prompt_source_for,
    resolve_command_template,
    resolve_run_templates,
)


class MemoryTemplates:
    def __init__(self, active=None, versions=None):
        self.active = active or []
        self.versions = versions or []

    async def active_prompt_template(self, scope_id, task):
        return next((item for item in self.active if item.scope_id == scope_id and item.task == task), None)

    async def prompt_template_version(self, version_id):
        return next(item for item in self.versions if item.id == version_id)

    async def pin_prompt_templates(self, command_id, templates):
        if command_id not in getattr(self, "pinned", {}):
            if not hasattr(self, "pinned"):
                self.pinned = {}
            self.pinned[command_id] = templates
        return self.pinned[command_id]


@pytest.mark.asyncio
async def test_template_resolution_inherits_defaults_and_honors_story_and_run_overrides():
    story_id = uuid4()
    global_override = PromptTemplateVersion(
        id=uuid4(), scope="global", scope_id=GLOBAL_PROMPT_SCOPE_ID,
        task="writing", name="全局", version=3, source_format="jinja2", source="你是 {{ story_title }} 的作者。",
    )
    story_override = PromptTemplateVersion(
        id=uuid4(), scope="story", scope_id=story_id,
        task="discussion", name="作品审阅", version=1, guidance="重点关注节奏",
    )
    explicit_old = PromptTemplateVersion(
        id=uuid4(), scope="global", scope_id=GLOBAL_PROMPT_SCOPE_ID,
        task="writing", name="单次选择", version=2, source_format="jinja2", source="完整自定义正文生成提示词。",
    )
    store = MemoryTemplates(
        active=[global_override, story_override],
        versions=[global_override, story_override, explicit_old],
    )
    resolved = await resolve_run_templates(store, story_id, [])
    assert [item.task for item in resolved] == list(PROMPT_TASKS)
    assert resolved[0].builtin is True
    assert next(item for item in resolved if item.task == "writing").id == global_override.id
    assert next(item for item in resolved if item.task == "discussion").id == story_override.id
    selected = await resolve_run_templates(
        store, story_id, [PromptTemplateSelection(task="writing", version_id=explicit_old.id)]
    )
    assert next(item for item in selected if item.task == "writing").id == explicit_old.id


@pytest.mark.asyncio
async def test_standalone_command_reuses_its_pinned_template_after_active_binding_changes():
    story_id, command_id = uuid4(), uuid4()
    first = PromptTemplateVersion(
        id=uuid4(), scope="story", scope_id=story_id, task="character_chat", name="v1", version=1,
        source_format="jinja2", source="第一版完整提示词。",
    )
    second = first.model_copy(update={"id": uuid4(), "version": 2, "source": "第二版完整提示词。"})
    store = MemoryTemplates(active=[first])
    initial = await resolve_command_template(store, command_id, story_id, "character_chat")
    store.active = [second]
    retried = await resolve_command_template(store, command_id, story_id, "character_chat")
    assert initial.id == retried.id == first.id
    assert retried.source == "第一版完整提示词。"


def test_legacy_command_snapshot_freezes_builtin_source_and_leaves_guidance_literal(monkeypatch):
    legacy = PromptTemplateVersion(
        id=uuid4(), scope="global", scope_id=GLOBAL_PROMPT_SCOPE_ID, task="writing", name="legacy", version=1,
        guidance="用户字面模板 {{ 不是变量 }}",
    )
    pinned = prompt_service.freeze_prompt_template(legacy)
    original_source = pinned.source
    monkeypatch.setattr(prompt_service, "default_prompt_source", lambda task: "部署后替换的新默认提示词。")
    source, guidance = prompt_source_for(pinned)
    rendered = render_prompt_source("writing", source, PromptRuntimeContext(), legacy_guidance=guidance)
    assert source == original_source
    assert "你是小说正文创作助手" in rendered.text
    assert rendered.text.endswith("用户字面模板 {{ 不是变量 }}")


def test_every_builtin_resource_is_a_renderable_full_role_and_task_prompt():
    templates = default_templates()
    assert [item.task for item in templates] == list(PROMPT_TASKS)
    assert [item.id for item in templates] == [item.id for item in default_templates()]
    for template in templates:
        assert template.source_format == "jinja2"
        assert template.source.strip()
        assert template.template_path.endswith(".j2")
        rendered = render_prompt_source(template.task, template.source, PromptRuntimeContext())
        assert rendered.text.strip()
        assert rendered.source_hash == template.source_hash


def test_legacy_version_projects_full_default_source_without_mutating_history():
    historical = PromptTemplateVersion(
        id=uuid4(), scope="story", scope_id=uuid4(), task="writing", name="旧模板", version=1,
        guidance="少用解释性独白",
    )
    projected = _project_version(historical)
    assert historical.source_format == "legacy_guidance" and historical.source == ""
    assert projected.source_format == "legacy_guidance" and projected.source == ""
    assert projected.editor_source.startswith(default_prompt_source("writing"))
    assert projected.editor_source.endswith("少用解释性独白")
    source, suffix = prompt_source_for(historical)
    rendered = render_prompt_source("writing", source, PromptRuntimeContext(), legacy_guidance=suffix)
    assert "你是小说正文创作助手" in rendered.text
    assert rendered.text.endswith("少用解释性独白")


def test_jinja_render_supports_user_context_and_rejects_invalid_or_unknown_variables():
    preview = preview_prompt_source(
        "writing",
        "你是{{ character_name }}。要求：{{ instruction }}。{{ '首次' if first_creation else '续写' }}。",
        PromptTemplatePreviewContext(character_name="林初", instruction="保留 {{ 原文 }}", first_creation=True),
    )
    assert preview.text == "你是林初。要求：保留 {{ 原文 }}。首次。"
    with pytest.raises(PromptRenderError, match="runtime_secret.*undefined"):
        render_prompt_source("writing", "你是{{ runtime_secret }}", PromptRuntimeContext())
    with pytest.raises(PromptRenderError, match="无法渲染"):
        render_prompt_source("writing", "{% if", PromptRuntimeContext())


def test_preview_context_defaults_do_not_invent_story_facts():
    context = PromptTemplatePreviewContext()
    assert not context.instruction and not context.story_title and not context.current_scene_summary
    rendered = preview_prompt_source("scene_planning", None, context)
    assert "首次创作必须建立故事骨架" not in rendered.text
