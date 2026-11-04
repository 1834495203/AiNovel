from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ainovel.core.agent import SubmitDraftAction, SubmitDraftArguments
from ainovel.core.config import Settings
from ainovel.core.domain import PROMPT_TASKS, Draft, DraftReviewResponse, Plan, Story, WritingState
from ainovel.core.prompting import default_template
from ainovel.llm.mock import MockModel
from ainovel.service.writing_agent import WritingAgent


def setup_agent():
    sink = SimpleNamespace(record=AsyncMock())
    model = MockModel(sink)
    model.choose_agent_tool = AsyncMock(side_effect=model.choose_agent_tool)
    store = SimpleNamespace(save_checkpoint=AsyncMock(), append_event=AsyncMock())
    agent = WritingAgent(model, None, store, Settings(_env_file=None))
    state = WritingState(
        command_id=uuid4(),
        source_revision_id=uuid4(),
        story=Story(title="测试"),
        instruction="按幕计划写正文",
        plan=Plan(chapter_plan="一章", scene_plan="收到信", character_names=[]),
        prompt_templates=[default_template("writing")],
    )
    return agent, model, state


async def test_one_prose_call_and_confirmation_without_model_call():
    agent, model, state = setup_agent()
    await agent.run(state)
    assert state.draft_status == "pending" and state.draft_version == 1
    assert state.story.scenes == []
    assert model.choose_agent_tool.await_count == 1
    state.review_response = DraftReviewResponse(decision="confirm", execution_command_id=uuid4())
    state.execution_command_id = state.review_response.execution_command_id
    await agent.run(state)
    assert state.draft_status == "confirmed"
    assert model.choose_agent_tool.await_count == 1
    assert not hasattr(agent, "graph")


async def test_revision_uses_same_agent_and_preserves_scene_plan():
    agent, model, state = setup_agent()
    await agent.run(state)
    plan = state.plan.model_copy(deep=True)
    state.review_response = DraftReviewResponse(
        decision="revise", execution_command_id=uuid4(), feedback="精简对白", text_only=True
    )
    state.execution_command_id = state.review_response.execution_command_id
    await agent.run(state)
    assert state.draft_version == 2 and state.plan == plan
    assert "精简对白" in state.draft.text
    assert model.choose_agent_tool.await_count == 2
    assert all(call.args[0].task == "writing" for call in model.choose_agent_tool.call_args_list)


def test_exactly_five_prompts_and_no_graph_package():
    assert tuple(PROMPT_TASKS) == (
        "creation_interview",
        "scene_planning",
        "writing",
        "discussion",
        "character_chat",
    )
    assert find_spec("langgraph") is None


def test_writing_tool_schema_cannot_modify_the_scene_plan():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubmitDraftArguments(
            draft=Draft(title="幕", text="正文", summary="摘要"), plan={"scene_plan": "不准改设定"}
        )
    assert SubmitDraftAction(
        arguments=SubmitDraftArguments(draft=Draft(title="幕", text="正文", summary="摘要"))
    )


async def test_failed_revision_resumes_feedback_and_tool_results(monkeypatch):
    from ainovel.core.discussion import (
        DiscussionToolResult,
        ReadStoryMaterialsAction,
        ReadStoryMaterialsArguments,
    )

    agent, model, state = setup_agent()
    await agent.run(state)
    state.review_response = DraftReviewResponse(
        decision="revise", execution_command_id=uuid4(), feedback="精简对白", text_only=True
    )
    state.execution_command_id = state.review_response.execution_command_id
    saved = []

    async def persist(item):
        saved.append(item.model_copy(deep=True))

    agent.store.save_checkpoint.side_effect = persist
    monkeypatch.setattr(
        "ainovel.service.writing_agent.read_tool",
        AsyncMock(return_value=DiscussionToolResult(name="read_story_materials", message="已读")),
    )
    model.choose_agent_tool.side_effect = [
        ReadStoryMaterialsAction(arguments=ReadStoryMaterialsArguments(scope="world")),
        RuntimeError("模拟调用失败"),
    ]
    with pytest.raises(RuntimeError, match="模拟调用失败"):
        await agent.run(state)
    restored = saved[-1]
    assert restored.draft_status == "writing"
    assert restored.review_response.feedback == "精简对白"
    assert len(restored.agent_results) == 1
    model.choose_agent_tool.side_effect = MockModel(SimpleNamespace(record=AsyncMock())).choose_agent_tool
    await agent.run(restored)
    assert restored.draft_status == "pending" and restored.draft_version == 2
    assert "精简对白" in restored.draft.text
    assert restored.review_response is None
    assert len(model.choose_agent_tool.call_args.args[0].tool_results) == 1
