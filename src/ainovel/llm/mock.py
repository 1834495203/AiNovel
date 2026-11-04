"""Deterministic offline adapter for the five ordinary agent modes."""

import re
from uuid import UUID

from ainovel.core.agent import SubmitCreationAction, SubmitDraftAction, SubmitDraftArguments
from ainovel.core.characters import Experience
from ainovel.core.discussion import (
    ReplyAction,
    SaveScenePlansAction,
    SaveScenePlansArguments,
    ScenePlanInput,
    ScenePlanUpdate,
    SearchStoryAction,
    SearchStoryArguments,
)
from ainovel.core.domain import CreationProposal, Draft, InterviewResult, StateUpdate, Trace
from ainovel.core.records import Entry, WorldSetting


class MockModel:
    def __init__(self, sink):
        self.sink = sink

    async def choose_agent_tool(self, context, command_id, *, prompt_template=None):
        task = getattr(context, "task", "scene_planning" if context.planning_mode else "discussion")
        goal = context.message
        if task == "creation_interview" and goal == "请开始了解我的小说构思":
            action = ReplyAction(reply="你想创作怎样的故事？请先描述基本世界观或故事方向。")
        elif task == "creation_interview":
            action = SubmitCreationAction(
                arguments=InterviewResult(
                    reply="已整理构思，请检查后确认建书。",
                    proposal=CreationProposal(
                        title="演示小说", world=WorldSetting(entries=[Entry(name="故事方向", value=goal)])
                    ),
                )
            )
        elif task == "writing":
            data = context.task_input
            old = data.get("draft") or {}
            plan = data.get("scene_plan") or {}
            text = (
                data.get("original")
                or old.get("text")
                or ("演示正文：" + goal + "。" + plan.get("scene_plan", ""))
            )
            if old or data.get("operation") == "rewrite_selection":
                text += "\n按意见修改：" + goal
            summary = "演示摘要：" + goal[:100]
            updates = []
            if data.get("scene_id"):
                updates = [
                    StateUpdate(
                        character_id=c.id,
                        new_experiences=[Experience(scene_id=UUID(data["scene_id"]), event=summary)],
                    )
                    for c in context.story.characters
                ]
            action = SubmitDraftAction(
                arguments=SubmitDraftArguments(
                    draft=Draft(title=old.get("title") or "当前幕", text=text, summary=summary),
                    updates=updates,
                )
            )
        elif task == "scene_planning" and not context.tool_results:
            data = context.task_input
            if data.get("target_id"):
                action = SaveScenePlansAction(
                    arguments=SaveScenePlansArguments(
                        update=[ScenePlanUpdate(id=UUID(data["target_id"]), summary=goal)]
                    )
                )
            else:
                count = (
                    min(int(re.search(r"(\d+)幕", goal).group(1)), 40) if re.search(r"(\d+)幕", goal) else 1
                )
                nodes = []
                parent = data.get("parent_id") or context.active_scene_leaf_id
                for index in range(count):
                    node = ScenePlanInput(
                        parent_id=parent,
                        title=f"幕 {len(context.scene_plans) + index + 1}",
                        summary=goal,
                        start_new_chapter=index == 0 and any(word in goal for word in ("新章", "另起一章")),
                    )
                    nodes.append(node)
                    parent = node.id
                action = SaveScenePlansAction(arguments=SaveScenePlansArguments(create=nodes))
        elif task == "discussion" and "召回" in goal and not context.tool_results:
            action = SearchStoryAction(arguments=SearchStoryArguments(query=goal))
        else:
            action = ReplyAction(
                reply=(
                    context.task_input.get("character_name", "") + "：" if task == "character_chat" else ""
                )
                + "已根据当前资料回应："
                + goal
            )
        await self.sink.record(
            command_id,
            Trace(
                node=task, summary="演示 agent 调用", model="mock", structured_output=action.model_dump_json()
            ),
        )
        return action
