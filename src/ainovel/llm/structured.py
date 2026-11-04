"""Forced structured-result strategy; no networking or retry loops."""

import json
from typing import Generic, TypeVar

from pydantic import BaseModel

from ainovel.core.config import Settings
from ainovel.core.context_layout import ContextField, custom_field_schemas, transmitted_ids
from ainovel.core.domain import PROMPT_TASKS, PromptTask, PromptTemplateVersion, WritingState
from ainovel.core.prompting import (
    default_template,
    prompt_context_for,
    prompt_source_for,
    prompt_version_label,
    render_prompt_source,
    workflow_template,
)
from ainovel.llm.contracts import CallMetadata, CompletionRequest
from ainovel.llm.errors import StructuredOutputError
from ainovel.llm.schemas import (
    ChatBody,
    FunctionChoice,
    FunctionDefinition,
    Message,
    ProviderMessage,
    ToolChoice,
    ToolDefinition,
    ToolParameters,
    project_context,
)

T = TypeVar("T", bound=BaseModel)


class StructuredResultDecoder(Generic[T]):
    def __init__(self, schema: type[T]) -> None:
        self.schema = schema
        self.tool_name = "submit_" + schema.__name__

    @property
    def repair_instruction(self) -> str:
        return (
            f"请根据原始要求重新调用 {self.tool_name}，一次提交完整且符合工具 schema 的参数，不要续写残片。"
            "简洁填写有效内容，省略没有内容的可选参数。"
        )

    def decode(self, message: ProviderMessage) -> T:
        if len(message.tool_calls) != 1:
            raise StructuredOutputError("本步骤需要且仅接受一次指定结果工具调用")
        call = message.tool_calls[0]
        if call.function.name != self.tool_name:
            raise StructuredOutputError("模型调用了不属于本步骤的结果工具")
        return self.schema.model_validate_json(call.function.arguments)


def build_structured_request(
    settings: Settings,
    schema: type[BaseModel],
    task: PromptTask,
    context: BaseModel,
    prompt_template: PromptTemplateVersion | None,
) -> CompletionRequest:
    if task not in PROMPT_TASKS:
        raise ValueError(f"未知提示词任务：{task}")
    if prompt_template is None and isinstance(context, WritingState):
        prompt_template = workflow_template(context, task)
    template = prompt_template or default_template(task)
    source, guidance = prompt_source_for(template)
    projected = (
        project_context(schema, context, settings.story_maintenance_interval)
        if isinstance(context, WritingState)
        else context
    )
    from ainovel.core.context_layout import sections_from
    runtime = prompt_context_for(schema.__name__, context).model_copy(update=sections_from(projected))
    rendered = render_prompt_source(
        task,
        source,
        runtime,
        legacy_guidance=guidance,
        template_path=template.template_path or None,
    )
    from ainovel.llm.context_messages import context_messages
    data_messages = context_messages(task, template, projected, runtime)
    tool_name = "submit_" + schema.__name__
    return CompletionRequest(
        metadata=CallMetadata(
            node=schema.__name__,
            summary=rendered.text[:160],
            prompt_version=prompt_version_label(template),
            error_label=schema.__name__,
        ),
        body=ChatBody(
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            messages=[
                Message(role="system", content=rendered.text),
                Message(role="system",content=json.dumps({"context_field_schema":ContextField.model_json_schema(),"custom_fields":custom_field_schemas(projected,transmitted_ids(data_messages))},ensure_ascii=False)),
                *data_messages,
            ],
            tools=[
                ToolDefinition(
                    function=FunctionDefinition(
                        name=tool_name,
                        description=schema.model_json_schema().get("description") or f"提交 {schema.__name__} 结果。",
                        parameters=ToolParameters(schema.model_json_schema()),
                    )
                )
            ],
            tool_choice=ToolChoice(function=FunctionChoice(name=tool_name)),
        ),
    )
