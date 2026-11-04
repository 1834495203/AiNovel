"""Shared read tools and ordinary bounded agent loop; no scheduler or graph engine."""

import inspect

from ainovel.core.agent import AgentContext, SubmitCreationAction, SubmitDraftAction
from ainovel.core.ai_contracts import business_tools
from ainovel.core.context_layout import clean
from ainovel.core.discussion import (
    DiscussionExcerpt,
    DiscussionToolResult,
    ReadStoryMaterialsAction,
    ReplyAction,
    SearchStoryAction,
)
from ainovel.core.domain import Event, Trace
from ainovel.core.fields import FieldTarget
from ainovel.core.story_fields import read_fields
from ainovel.service.discussion_tools import context_story


def context_for(story, revision_id, task, message, *, history=None, task_input=None, results=None, recent=3):
    from ainovel.core.discussion import ScenePlanBrief

    return AgentContext(
        task=task,
        planning_mode=task == "scene_planning",
        story=context_story(story, revision_id, recent),
        history=history or [],
        message=message,
        scene_plans=[
            ScenePlanBrief(
                id=n.id,
                parent_id=n.parent_id,
                title=n.title,
                summary=n.plan.scene_plan,
                written=any(s.id == n.id for s in story.scenes),
            )
            for n in story.planned_scenes
        ],
        active_scene_leaf_id=story.active_scene_leaf_id,
        task_input=task_input or {},
        tool_results=results or [],
    )


async def read_tool(services, story, revision_id, action, command_id):
    if isinstance(action, SearchStoryAction):
        chunks = await services.retrieval.narrative(revision_id, action.arguments.query, command_id)
        return DiscussionToolResult(
            name="search_story",
            message=f"找到 {len(chunks)} 个片段",
            excerpts=[DiscussionExcerpt(title=c.kind, text=c.content) for c in chunks],
        )
    if isinstance(action, ReadStoryMaterialsAction):
        args = action.arguments
        try:
            if args.scope == "scene_plan":
                items = [
                    {
                        "id": str(n.id),
                        "name": n.title,
                        "value": n.plan.scene_plan,
                        "children": [{"name": "父幕", "value": str(n.parent_id) if n.parent_id else None}],
                    }
                    for n in story.planned_scenes
                    if args.entity_id is None or n.id == args.entity_id
                ]
            else:
                items = [
                    item.model_dump(mode="json")
                    for item in read_fields(story, FieldTarget(scope=args.scope, entity_id=args.entity_id))
                ]
            page = items[args.offset : args.offset + args.limit]
            return DiscussionToolResult(
                name="read_story_materials",
                message=f"读取 {len(page)} 项",
                materials=[clean(item, truncate=False) for item in page],
                next_offset=args.offset + len(page) if args.offset + len(page) < len(items) else None,
            )
        except ValueError as error:
            return DiscussionToolResult(name="read_story_materials", success=False, message=str(error))
    raise ValueError("本模式不允许该工具")


async def run_agent(
    services, command_id, task, template, build_context, execute_tool, *, results=None, on_result=None
):
    results = results if results is not None else []
    seen = {r.name + r.arguments_json for r in results}
    allowed = {tool.name for tool in business_tools(task)}
    for _ in range(services.settings.agent_max_steps):
        context = build_context(results)
        action = await services.model.choose_agent_tool(context, command_id, prompt_template=template)
        if isinstance(action, ReplyAction):
            return action
        if action.kind not in allowed:
            raise ValueError(
                "本模式只能查询和讨论，不能调用修改工具" if task == "discussion" else "本模式不允许该工具"
            )
        if isinstance(action, (SubmitDraftAction, SubmitCreationAction)):
            return action
        signature = action.kind + action.arguments.model_dump_json()
        if signature in seen:
            return ReplyAction(reply="重复工具调用已停止；没有重复执行操作。")
        seen.add(signature)
        await services.store.append_event(
            Event(
                command_id=command_id,
                actor="ai",
                type="step.started",
                trace=Trace(
                    node=action.kind, summary="调用工具", structured_input=action.arguments.model_dump_json()
                ),
            )
        )
        try:
            outcome = await execute_tool(action)
        except ValueError as error:
            outcome = DiscussionToolResult(name=action.kind, success=False, message=str(error))
        outcome.call_id = action.call_id
        outcome.arguments_json = action.arguments.model_dump_json()
        results.append(outcome)
        if on_result:
            answer = on_result(results)
            if inspect.isawaitable(answer):
                await answer
        await services.store.append_event(
            Event(
                command_id=command_id,
                actor="ai",
                type="step.completed",
                trace=Trace(
                    node=action.kind, summary=outcome.message, structured_output=outcome.model_dump_json()
                ),
            )
        )
    return ReplyAction(reply="本轮工具调用已停止，已完成的操作保留，未继续重复消耗。")
