from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ainovel.core.domain import (
    CommandOutput,
    CommandSubmission,
    GenerateRequest,
    Plan,
    PlannedScene,
    PlanSceneRequest,
    Scene,
    SelectSceneBranchRequest,
    Story,
)
from ainovel.service.commands import GenerateHandler
from ainovel.service.scene_planning import (
    PlanSceneHandler,
    SelectSceneBranchHandler,
    next_scene,
    scene_path,
)


def node(parent=None):
    return PlannedScene(
        parent_id=parent,
        title="夜访",
        plan=Plan(chapter_plan="第一章", scene_plan="主角发现线索", character_names=[]),
    )


def services(story):
    from ainovel.core.config import Settings
    from ainovel.llm.mock import MockModel
    model=MockModel(SimpleNamespace(record=AsyncMock()))
    model.choose_agent_tool=AsyncMock(side_effect=model.choose_agent_tool)
    return SimpleNamespace(source=AsyncMock(return_value=story),model=model,settings=Settings(_env_file=None),
        commit=AsyncMock(return_value=CommandOutput(message="保存")),
        store=SimpleNamespace(checkpoint=AsyncMock(return_value=None),append_event=AsyncMock(),active_prompt_template=AsyncMock(return_value=None),pin_prompt_templates=AsyncMock(side_effect=lambda command,templates:templates)))


def submission(story, payload):
    return CommandSubmission(story_id=story.id, base_revision_id=uuid4(), payload=payload)


def test_branch_selection_keeps_written_prefix_and_next_scene():
    a = node()
    b = node(a.id)
    c = node(a.id)
    story = Story(title="测试", planned_scenes=[a, b, c], active_scene_leaf_id=b.id)
    assert next_scene(story).id == a.id
    story.scenes.append(Scene(id=a.id, chapter_id=uuid4(), title="正文", text="正文", summary="摘要"))
    assert next_scene(story).id == b.id
    story.active_scene_leaf_id = c.id
    assert next_scene(story).id == c.id
    other = node()
    story.planned_scenes.append(other)
    story.active_scene_leaf_id = other.id
    with pytest.raises(ValueError, match="已经写出"):
        next_scene(story)


def test_invalid_tree_is_rejected():
    story = Story(title="测试")
    with pytest.raises(ValueError, match="不存在"):
        scene_path(story, uuid4())
    a = node()
    a.parent_id = a.id
    story.planned_scenes = [a]
    with pytest.raises(ValueError, match="循环"):
        scene_path(story, a.id)


async def test_settings_create_edit_and_branch_without_prose():
    story = Story(title="测试")
    svc = services(story)
    handler = PlanSceneHandler(svc)
    request = PlanSceneRequest(instruction="发现一封信")
    await handler.execute(submission(story, request), request)
    a = story.planned_scenes[0]
    assert story.scenes == [] and story.active_scene_leaf_id == a.id
    for instruction in ("去码头", "另一个可能：去邮局"):
        request = PlanSceneRequest(instruction=instruction, parent_id=a.id)
        await handler.execute(submission(story, request), request)
    assert len(story.planned_scenes) == 3
    request = PlanSceneRequest(instruction="修改线索", target_id=a.id)
    await handler.execute(submission(story, request), request)
    assert len(story.planned_scenes) == 3 and not story.scenes
    story.scenes.append(Scene(id=a.id, chapter_id=uuid4(), title="正文", text="正文", summary="摘要"))
    with pytest.raises(ValueError, match="锁定"):
        await handler.execute(submission(story, request), request)


async def test_write_requires_existing_next_scene():
    story = Story(title="测试")
    svc = services(story)
    request = GenerateRequest(instruction="写正文")
    with pytest.raises(ValueError, match="设定模式"):
        await GenerateHandler(svc).execute(submission(story, request), request)
    a = node()
    b = node(a.id)
    story.planned_scenes = [a, b]
    story.active_scene_leaf_id = b.id
    request.planned_scene_id = b.id
    with pytest.raises(ValueError, match="下一幕"):
        await GenerateHandler(svc).execute(submission(story, request), request)
    svc.model.choose_agent_tool.assert_not_awaited()


async def test_branch_command_validates_written_history():
    a = node()
    b = node()
    story = Story(
        title="测试",
        planned_scenes=[a, b],
        active_scene_leaf_id=a.id,
        scenes=[Scene(id=a.id, chapter_id=uuid4(), title="正文", text="正文", summary="摘要")],
    )
    svc = services(story)
    request = SelectSceneBranchRequest(leaf_id=b.id)
    with pytest.raises(ValueError, match="已经写出"):
        await SelectSceneBranchHandler(svc).execute(submission(story, request), request)
    svc.commit.assert_not_awaited()


def test_agent_batch_creates_chain_and_updates_multiple_unwritten_scenes():
    from ainovel.core.discussion import SaveScenePlansArguments, ScenePlanInput, ScenePlanUpdate
    from ainovel.service.scene_planning import apply_scene_batch

    a, b = uuid4(), uuid4()
    story = Story(title="测试")
    apply_scene_batch(
        story,
        SaveScenePlansArguments(
            create=[
                ScenePlanInput(id=a, title="来信", summary="收到信"),
                ScenePlanInput(id=b, parent_id=a, title="出发", summary="去码头"),
            ]
        ),
    )
    assert [n.id for n in scene_path(story, story.active_scene_leaf_id)] == [a, b]
    apply_scene_batch(
        story,
        SaveScenePlansArguments(
            update=[
                ScenePlanUpdate(id=a, summary="收到蓝色信封"),
                ScenePlanUpdate(id=b, summary="去邮局"),
            ]
        ),
    )
    assert [n.plan.scene_plan for n in story.planned_scenes] == ["收到蓝色信封", "去邮局"]
    story.scenes.append(Scene(id=a, chapter_id=uuid4(), title="正文", text="正文", summary="摘要"))
    with pytest.raises(ValueError, match="锁定"):
        apply_scene_batch(story, SaveScenePlansArguments(update=[ScenePlanUpdate(id=a, summary="改剧情")]))


def test_old_written_scene_is_valid_parent_for_new_plan():
    from ainovel.core.discussion import SaveScenePlansArguments, ScenePlanInput
    from ainovel.service.scene_planning import apply_scene_batch
    anchor = Scene(chapter_id=uuid4(),title="旧正文",text="正文",summary="摘要")
    story = Story(title="旧小说",scenes=[anchor])
    apply_scene_batch(story,SaveScenePlansArguments(create=[ScenePlanInput(parent_id=anchor.id,title="下一幕",summary="接着写")]))
    assert next_scene(story).parent_id == anchor.id
