"""Native tool protocol for the five business agents, using one shared gateway."""

from ainovel.core.agent import AgentAction
from ainovel.core.config import Settings
from ainovel.core.discussion import (
    DiscussionContext,
    ReadStoryMaterialsAction,
    ReadStoryMaterialsArguments,
    ReplyAction,
    SaveScenePlansAction,
    SaveScenePlansArguments,
    SearchStoryAction,
    SearchStoryArguments,
)
from ainovel.core.domain import PromptTemplateVersion
from ainovel.core.prompting import (
    default_template,
    prompt_context_for,
    prompt_source_for,
    prompt_version_label,
    render_prompt_source,
)
from ainovel.llm.contracts import CallMetadata, CompletionRequest
from ainovel.llm.errors import StructuredOutputError
from ainovel.llm.schemas import (
    ChatBody,
    FunctionCall,
    FunctionDefinition,
    Message,
    ProviderMessage,
    ToolCall,
    ToolDefinition,
    ToolParameters,
)


def build_agent_request(
    settings: Settings, context: DiscussionContext, prompt_template: PromptTemplateVersion | None
) -> CompletionRequest:
    task = getattr(context, "task", "scene_planning" if context.planning_mode else "discussion")
    selected_template = prompt_template or default_template(task)
    source, legacy_guidance = prompt_source_for(selected_template)
    prompt_version = prompt_version_label(selected_template)
    import json

    from ainovel.core.ai_contracts import business_tools
    from ainovel.core.context_layout import ContextField, custom_field_schemas, transmitted_ids
    from ainovel.llm.context_messages import context_messages

    contracts = business_tools(task)
    definitions = [
        ToolDefinition(
            function=FunctionDefinition(
                name=tool.name, description=tool.description, parameters=ToolParameters(tool.parameters)
            )
        )
        for tool in contracts
    ]

    def fields(items):
        return [
            {
                "id": str(item.id),
                "name": item.name,
                "value": item.value,
                "description": item.description,
                "children": fields(item.children),
            }
            for item in items
            if item.value is not None or item.children or item.description
        ]

    payload = {
        "title": context.story.title,
        "world": fields(context.story.world.entries),
        "preferences": fields(context.story.preferences.entries),
        "writer": fields(context.story.writer.entries),
        "characters": [
            {
                "id": str(c.id),
                "name": c.name,
                "profile": fields(c.snapshot.profile.entries),
                "state": fields(c.snapshot.state.entries),
            }
            for c in context.story.characters
        ],
        "recent_story": [
            {"title": x.title, "summary": x.summary or x.text[:500]} for x in context.story.recent_scenes[-3:]
        ],
        "scene_plans": [n.model_dump(mode="json") for n in context.scene_plans],
        "active_scene_leaf_id": str(context.active_scene_leaf_id) if context.active_scene_leaf_id else None,
    }
    payload.update(getattr(context, "task_input", {}))
    from ainovel.core.context_layout import sections_from

    runtime = prompt_context_for("DiscussionAction", context).model_copy(update=sections_from(payload))
    rendered = render_prompt_source(
        task,
        source,
        runtime,
        legacy_guidance=legacy_guidance,
        template_path=selected_template.template_path or None,
    )
    data_messages = context_messages(task, selected_template, payload, runtime)
    return_schemas = {}
    tool_return_types = {}
    for contract in contracts:
        if contract.returns == contract.parameters:
            tool_return_types[contract.name] = {"same_as_parameters": contract.name}
        else:
            key = contract.returns.get("title", contract.name)
            return_schemas[key] = contract.returns
            tool_return_types[contract.name] = key
    body = ChatBody(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        messages=[
            Message(role="system", content=rendered.text),
            Message(
                role="system",
                content=json.dumps(
                    {
                        "context_field_schema": ContextField.model_json_schema(),
                        "custom_fields": custom_field_schemas(context.story, transmitted_ids(data_messages)),
                        "tool_returns": {"schemas": return_schemas, "tools": tool_return_types},
                    },
                    ensure_ascii=False,
                ),
            ),
            *data_messages,
            *[Message(role=t.role, content=t.content[-1500:]) for t in context.history[-8:]],
            Message(role="user", content=context.message),
        ],
        tools=definitions,
        tool_choice="auto",
    )
    label = {
        "creation_interview": "创建小说",
        "scene_planning": "幕设定",
        "writing": "正文写作",
        "discussion": "故事讨论",
        "character_chat": "角色对话",
    }[task]
    for i, result in enumerate(context.tool_results):
        call_id = result.call_id or f"tool_{i}"
        body.messages.extend(
            [
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=call_id,
                            function=FunctionCall(name=result.name, arguments=result.arguments_json or "{}"),
                        )
                    ],
                ),
                Message(
                    role="tool",
                    tool_call_id=call_id,
                    content=result.model_dump_json(exclude={"call_id", "arguments_json"}),
                ),
            ]
        )
    return CompletionRequest(
        body=body,
        metadata=CallMetadata(
            node=task,
            summary=f"{label}，按需使用工具",
            prompt_version=prompt_version,
            error_label=label,
        ),
    )


class AgentResultDecoder:
    def __init__(self, task="discussion", planning_mode: bool = False):
        self.task = "scene_planning" if planning_mode else task
        self.planning_mode = self.task == "scene_planning"

    @property
    def repair_instruction(self) -> str:
        return "最多调用一个已定义工具，提交完整有效参数；如果无需工具，直接回复。"

    def decode(self, message: ProviderMessage) -> AgentAction:
        calls = message.tool_calls
        if len(calls) > 1:
            raise StructuredOutputError("每步只接受一个工具调用")
        if not calls:
            content = (message.content or "").strip()
            if not content:
                raise StructuredOutputError("模型没有返回回复或工具调用")
            return ReplyAction(reply=content)
        call = calls[0]
        from ainovel.core.agent import SubmitCreationAction, SubmitDraftAction, SubmitDraftArguments
        from ainovel.core.ai_contracts import business_tools
        from ainovel.core.domain import InterviewResult

        if call.function.name not in {tool.name for tool in business_tools(self.task)}:
            raise StructuredOutputError("本模式没有授权该工具")
        if call.function.name == "submit_draft":
            return SubmitDraftAction(
                call_id=call.id, arguments=SubmitDraftArguments.model_validate_json(call.function.arguments)
            )
        if call.function.name == "submit_creation":
            return SubmitCreationAction(
                call_id=call.id, arguments=InterviewResult.model_validate_json(call.function.arguments)
            )
        if call.function.name == "save_scene_plans" and self.planning_mode:
            return SaveScenePlansAction(
                call_id=call.id,
                arguments=SaveScenePlansArguments.model_validate_json(call.function.arguments),
            )
        if call.function.name == "read_story_materials":
            return ReadStoryMaterialsAction(
                call_id=call.id,
                arguments=ReadStoryMaterialsArguments.model_validate_json(call.function.arguments),
            )
        if call.function.name == "search_story":
            return SearchStoryAction(
                call_id=call.id, arguments=SearchStoryArguments.model_validate_json(call.function.arguments)
            )
        raise StructuredOutputError("故事讨论仅允许查询工具，不能修改资料")
