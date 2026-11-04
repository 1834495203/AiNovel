"""Typed AI contracts, exported independently from editable prompt text."""

from pydantic import Field, JsonValue

from ainovel.core.base import Model
from ainovel.core.discussion import (
    ReadStoryMaterialsArguments,
    SaveScenePlansArguments,
    SearchStoryArguments,
    ToolResult,
)


class ToolContract(Model):
    name: str = Field(description="模型调用的工具名称。")
    description: str = Field(description="用途与调用边界，来自参数模型 description 或显式覆盖。")
    parameters: dict[str, JsonValue] = Field(description="由 Pydantic 导出的参数 JSON Schema。")
    returns: dict[str, JsonValue] = Field(description="由 Pydantic 导出的公开返回值 JSON Schema。")


class AIContractCatalog(Model):
    tools: list[ToolContract] = Field(description="当前 agent 可调用的工具及独立参数、返回值 schema。")
    results: dict[str, dict[str, JsonValue]] = Field(description="正文等结构化任务使用的结果 schema。")
    context_fields: dict[str, JsonValue] = Field(description="上下文中实际字段值的结构，独立于用户字段定义。")


def tool_contract(
    name: str, arguments: type[Model], result: type[Model], description: str | None = None
) -> ToolContract:
    schema = arguments.model_json_schema()
    return ToolContract(
        name=name,
        description=description or schema.get("description", arguments.__name__),
        parameters=schema,
        returns=result.model_json_schema(),
    )


def agent_tools(planning: bool = True) -> list[ToolContract]:
    tools = [tool_contract("search_story", SearchStoryArguments, ToolResult)]
    if planning:
        tools.append(tool_contract("read_story_materials", ReadStoryMaterialsArguments, ToolResult))
        tools.append(tool_contract("save_scene_plans", SaveScenePlansArguments, ToolResult))
    return tools


def business_tools(task):
    from ainovel.core.agent import SubmitDraftArguments
    from ainovel.core.domain import InterviewResult, WritingDraft

    if task == "creation_interview":
        return [tool_contract("submit_creation", InterviewResult, InterviewResult)]
    if task == "scene_planning":
        return agent_tools(True)
    if task == "writing":
        return [
            *agent_tools(False),
            tool_contract("read_story_materials", ReadStoryMaterialsArguments, ToolResult),
            tool_contract("submit_draft", SubmitDraftArguments, WritingDraft),
        ]
    if task == "character_chat":
        return [tool_contract("read_story_materials", ReadStoryMaterialsArguments, ToolResult)]
    return [
        *agent_tools(False),
        tool_contract("read_story_materials", ReadStoryMaterialsArguments, ToolResult),
    ]


def schema_catalog() -> AIContractCatalog:
    from ainovel.core.context_layout import ContextField
    from ainovel.core.domain import ChatResult, Draft, InterviewResult

    tools = {
        tool.name: tool
        for task in ("creation_interview", "scene_planning", "writing", "discussion", "character_chat")
        for tool in business_tools(task)
    }
    return AIContractCatalog(
        tools=list(tools.values()),
        results={model.__name__: model.model_json_schema() for model in (Draft, InterviewResult, ChatResult)},
        context_fields=ContextField.model_json_schema(),
    )
