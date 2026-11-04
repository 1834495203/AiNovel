"""Typed agent protocol shared by the five business modes."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from ainovel.core.base import Model
from ainovel.core.discussion import (
    DiscussionContext,
    ReadStoryMaterialsAction,
    ReplyAction,
    SaveScenePlansAction,
    SearchStoryAction,
)
from ainovel.core.domain import Draft, EditFieldsRequest, InterviewResult, PromptTask, StateUpdate


class SubmitDraftArguments(Model):
    """提交本轮待用户确认的正文及其实际事实变化；此工具不修改正式小说。"""

    draft: Draft = Field(description="本轮完整正文及摘要；局部改写任务只提交替换片段。")
    updates: list[StateUpdate] = Field(
        default_factory=list, description="正文实际造成的角色变化；没有则留空。"
    )
    materials: list[EditFieldsRequest] = Field(
        default_factory=list, description="随正文确认一起提交的资料变化；不能修改已写幕的设定。"
    )


class SubmitDraftAction(Model):
    kind: Literal["submit_draft"] = "submit_draft"
    call_id: str = ""
    arguments: SubmitDraftArguments


class SubmitCreationAction(Model):
    kind: Literal["submit_creation"] = "submit_creation"
    call_id: str = ""
    arguments: InterviewResult


AgentAction = Annotated[
    SearchStoryAction
    | ReadStoryMaterialsAction
    | SaveScenePlansAction
    | SubmitDraftAction
    | SubmitCreationAction
    | ReplyAction,
    Field(discriminator="kind"),
]


class AgentContext(DiscussionContext):
    task: PromptTask
    task_input: dict[str, JsonValue] = Field(
        default_factory=dict, description="本轮任务输入，不含服务对象或存储 schema。"
    )
