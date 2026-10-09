"""Immutable full-prompt sources and their project/global bindings."""

from uuid import UUID

from ainovel.core.domain import (
    PROMPT_TASKS,
    PromptTask,
    PromptTemplateLibrary,
    PromptTemplateSelection,
    PromptTemplateTaskLibrary,
    PromptTemplateVersion,
)
from ainovel.core.prompting import (
    GLOBAL_PROMPT_SCOPE_ID,
    PROMPT_CONTEXT_VARIABLES,
    PromptRuntimeContext,
    default_prompt_source,
    prompt_context_schema,
    prompt_metadata,
    prompt_source_hash,
    render_prompt_source,
)
from ainovel.core.prompting import default_template as default_template
from ainovel.core.prompting import prompt_source_for as prompt_source_for
from ainovel.mapper.store import Store

TASKS: tuple[PromptTask, ...] = PROMPT_TASKS


def default_templates() -> list[PromptTemplateVersion]:
    return [default_template(task) for task in TASKS]


def freeze_prompt_template(template: PromptTemplateVersion) -> PromptTemplateVersion:
    """Copy the current builtin resource into a command snapshot for legacy rows."""
    if template.source:
        return template
    source = default_prompt_source(template.task)
    return template.model_copy(update={
        "source": source,
        "source_hash": prompt_source_hash(
            source, template.guidance if template.source_format == "legacy_guidance" else ""
        ),
        "template_path": template.template_path or prompt_metadata(template.task).template_path,
        "context_variables": template.context_variables or [
            item.name for item in prompt_context_schema(template.task)
        ],
        "context_schema": template.context_schema or prompt_context_schema(template.task),
    })


async def resolve_run_templates(
    store: Store,
    story_id: UUID,
    selections: list[PromptTemplateSelection],
) -> list[PromptTemplateVersion]:
    result: list[PromptTemplateVersion] = []
    for task in TASKS:
        chosen = await store.active_prompt_template(GLOBAL_PROMPT_SCOPE_ID, task) or default_template(task)
        story_choice = await store.active_prompt_template(story_id, task)
        if story_choice is not None:
            chosen = story_choice
        selection = next((item for item in selections if item.task == task), None)
        if selection is not None:
            selected = await store.prompt_template_version(selection.version_id)
            if selected.task != task or selected.scope == "story" and selected.scope_id != story_id:
                raise ValueError("模板版本不属于当前写作任务或作品")
            chosen = selected
        result.append(freeze_prompt_template(chosen))
    if len(selections) != len({item.task for item in selections}):
        raise ValueError("同一任务只能选择一个提示词模板版本")
    return result


async def resolve_prompt_template(store: Store, story_id: UUID | None, task: PromptTask) -> PromptTemplateVersion:
    global_active = await store.active_prompt_template(GLOBAL_PROMPT_SCOPE_ID, task)
    if story_id is not None:
        story_active = await store.active_prompt_template(story_id, task)
        if story_active is not None:
            return story_active
    return global_active or default_template(task)


async def resolve_command_template(
    store: Store, command_id: UUID, story_id: UUID | None, task: PromptTask
) -> PromptTemplateVersion:
    template = await resolve_prompt_template(store, story_id, task)
    pinned = await store.pin_prompt_templates(command_id, [freeze_prompt_template(template)])
    selected = next((item for item in pinned if item.task == task), None)
    if selected is None:
        raise ValueError("命令已固定的提示词集合缺少当前任务")
    return selected


async def template_library(store: Store, story_id: UUID | None) -> PromptTemplateLibrary:
    global_versions = await store.prompt_template_versions(GLOBAL_PROMPT_SCOPE_ID)
    story_versions = await store.prompt_template_versions(story_id) if story_id else []
    libraries: list[PromptTemplateTaskLibrary] = []
    for task in TASKS:
        metadata = prompt_metadata(task)
        builtin = default_template(task)
        global_active = _project_version(await store.active_prompt_template(GLOBAL_PROMPT_SCOPE_ID, task) or builtin)
        stored_story_active = await store.active_prompt_template(story_id, task) if story_id else None
        story_active = _project_version(stored_story_active) if stored_story_active else None
        versions = [item for item in [*global_versions, *story_versions] if item.task == task]
        projected_versions = [_project_version(item) for item in versions]
        libraries.append(
            PromptTemplateTaskLibrary(
                task=task,
                label=metadata.label,
                description=metadata.description,
                template_path=metadata.template_path,
                context_variables=[item.name for item in prompt_context_schema(task)],
                context_schema=prompt_context_schema(task),
                editor_source=global_active.editor_source,
                active=story_active or global_active,
                global_active=global_active,
                story_active=story_active,
                versions=projected_versions,
            )
        )
    return PromptTemplateLibrary(tasks=libraries)


def _project_version(version: PromptTemplateVersion) -> PromptTemplateVersion:
    source = version.source if version.source_format == "jinja2" and version.source else default_prompt_source(version.task)
    guidance = version.guidance if version.source_format == "legacy_guidance" else ""
    editor_source = source
    if guidance:
        editor_source += "\n\n【旧版创作策略补充】\n" + guidance.strip()
    return version.model_copy(update={
        "editor_source": editor_source,
        "template_path": version.template_path or prompt_metadata(version.task).template_path,
        "source_hash": version.source_hash or prompt_source_hash(source, guidance),
        "context_variables": version.context_variables or PROMPT_CONTEXT_VARIABLES,
        "context_schema": version.context_schema or prompt_context_schema(version.task),
    })


def validate_prompt_source(task: PromptTask, source: str) -> tuple[str, list[str]]:
    rendered = render_prompt_source(task, source, PromptRuntimeContext())
    return rendered.source_hash, rendered.context_variables
