import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_studio import client as studio_client  # noqa: F401
from test_studio import fixture

from ainovel.core.config import Settings
from ainovel.core.entities import Attribute, Entity
from ainovel.core.performance_timeline import (
    active_path,
    capture_state,
    changes_between,
    record_change,
    replay,
    rewind,
    timeline_context,
)
from ainovel.core.studio import StudioAction, StudioNovel, StudioStep
from ainovel.core.studio_state import apply_patch, emit, fork, present, role_context, role_states
from ainovel.core.studio_tools import RewindPerformance, tools_for
from ainovel.service.studio import StudioRunner


@pytest.fixture
async def client(studio_client):  # noqa: F811
    yield studio_client


async def save(novel):
    pass


def runner(novel, actor=None):
    return StudioRunner(novel, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor)


async def execute(engine, scene, tool_name, **arguments):
    return await engine.execute("gm", scene, StudioAction(name=tool_name, arguments=arguments))


@pytest.mark.parametrize(
    "before,after",
    [
        ({"s": ["a"]}, {"s": ["a", "b"]}),
        ({"x": [1, 2, 3]}, {"x": [1]}),
        ({"a/b": {"~key": [1]}}, {"a/b": {"~key": [2, 3]}}),
        ({"old": True}, {"new": None}),
        ({"v": True}, {"v": 1}),
        ({"v": [1]}, {"v": {"a": 1}}),
    ],
)
def test_only_forward_changes_reproduce_state_without_mutating_start(before, after):
    original = deepcopy(before)
    changes = changes_between(before, after)
    assert json.dumps(apply_patch(before, changes), sort_keys=True) == json.dumps(after, sort_keys=True)
    assert before == original
    if before == {"s": ["a"]}:
        assert [(c.op, c.path, c.value) for c in changes] == [("add", "/s/1", "b")]


async def test_rewind_restores_roles_fields_presence_events_and_new_characters_by_forward_replay():
    n, scene, a, _ = fixture()
    scene.messages = []
    initial = capture_state(n, scene)
    engine = runner(n)
    created = await execute(engine, scene, "create_character", name="丙")
    c_id = created.data["id"]
    await execute(engine, scene, "enter_character", character=a.id)
    await execute(engine, scene, "enter_character", character=c_id)
    group = await execute(engine, scene, "say_to_group", text="灯亮了")
    event_id = group.data["event_id"]

    async def actor(mode, data, history, request):
        if data["self"]["id"] == c_id:
            return StudioAction(name="leave", arguments={"reason": "丙先离开"})
        if not history:
            return StudioAction(
                name="edit_self",
                arguments={
                    "operations": [
                        {"op": "replace", "path": "/special_attributes/0/current_value", "value": "警惕"}
                    ]
                },
            )
        return StudioAction(name="reply", text="甲决定先等候")

    engine.actor = actor
    await engine.role(scene, a, "自行决定")
    await execute(engine, scene, "remove_character", character=a.id)
    await engine.role(scene, next(c for c in n.characters if c.id == c_id), "自行决定")
    assert present(scene) == []
    assert role_states(n, scene)[a.id].special_attributes[0].current_value == "警惕"
    old_head, old_base = scene.timeline.head, deepcopy(scene.timeline.base)
    calls = n.calls

    # Reloading the aggregate retains the start, operation links and active position.
    n = StudioNovel.model_validate_json(n.model_dump_json())
    scene = n.scenes[-1]
    result = rewind(n, scene, steps=1)
    assert result["present_count"] == 1 and present(scene) == [c_id]
    result = rewind(n, scene, target=event_id)
    assert result["position"] == 4 and present(scene) == [a.id, c_id]
    assert role_states(n, scene)[a.id].special_attributes[0].current_value == "平静"
    assert [e.text for e in scene.events][-1] == "灯亮了"
    assert "甲决定先等候" not in json.dumps(role_context(n, scene, a.id), ensure_ascii=False)
    assert "changes" not in timeline_context(scene)["steps"][0]
    assert rewind(n, scene, target="step:3")["position"] == 3
    with pytest.raises(ValueError, match="回撤步骤编号无效"):
        rewind(n, scene, target="step:4")
    rewind(n, scene, target="start")
    assert capture_state(n, scene) == initial
    assert n.calls == calls and scene.timeline.base == old_base
    assert len(scene.timeline.operations) == 10
    assert sum(op.kind == "gm_direction" for op in scene.timeline.operations) == 2
    assert active_path(scene.timeline) == []
    # Choosing a new continuation appends a new path; the previous path is not rewritten.
    new = await execute(runner(n), scene, "create_character", name="丁")
    assert scene.timeline.operations[-1].parent == ""
    assert len(active_path(scene.timeline)) == 1 and len(scene.timeline.operations) == 11
    assert [c.name for c in n.characters][-1] == "丁"
    assert any(c["name"] == "丙" for c in replay(scene.timeline, old_head)["characters"])
    assert new.data["operation"]["id"] == scene.timeline.head
    with pytest.raises(ValueError, match="当前可回溯路径"):
        rewind(n, scene, target=event_id)
    root_copy = replay(scene.timeline, "")
    root_copy["world"].append({"name": "不能污染起点"})
    assert scene.timeline.base == old_base


async def test_global_fields_chapter_and_reset_are_replayed_but_system_settings_are_preserved():
    n, scene, a, _ = fixture()
    engine = runner(n)
    await execute(
        engine,
        scene,
        "edit_fields",
        scope="world",
        operations=[{"op": "add", "path": "/-", "value": {"name": "新世界", "type": "world"}}],
    )
    await execute(
        engine,
        scene,
        "edit_fields",
        scope="characters",
        target=a.id,
        operations=[{"op": "replace", "path": "/name", "value": "甲的新名字"}],
    )
    await execute(
        engine,
        scene,
        "edit_fields",
        scope="chapters",
        target=scene.chapter_id,
        operations=[{"op": "replace", "path": "/name", "value": "新章名"}],
    )
    await execute(engine, scene, "enter_character", character=a.id)
    await execute(engine, scene, "say_to_group", text="看见了灯塔")
    before_reset = capture_state(n, scene)
    await execute(engine, scene, "reset_performance")
    assert scene.events == [] and scene.abandoned
    rewind(n, scene, steps=1)
    assert capture_state(n, scene) == before_reset
    n.chapters[0].summary = "稍后生成的系统摘要"
    n.compression.max_calls = 7
    n.input_tokens = 123
    rewind(n, scene, target="start")
    assert n.world == [] and n.characters[0].name == "甲" and n.chapters[0].entity.name == "第一章"
    assert (
        n.chapters[0].summary == "稍后生成的系统摘要"
        and n.compression.max_calls == 7
        and n.input_tokens == 123
    )


async def test_replay_restores_both_room_counter_field_and_actual_membership():
    n, scene, a, _ = fixture()
    room = Entity(
        name="房间",
        type="location",
        special_attributes=[Attribute(key="人数", value_type="int", current_value=0)],
    )
    n.world = [room]
    engine = runner(n)
    await execute(engine, scene, "enter_character", character=a.id)
    count_one = await execute(
        engine,
        scene,
        "edit_fields",
        scope="world",
        target=room.id,
        operations=[{"op": "replace", "path": "/special_attributes/0/current_value", "value": 1}],
    )
    await execute(engine, scene, "remove_character", character=a.id)
    await execute(
        engine,
        scene,
        "edit_fields",
        scope="world",
        target=room.id,
        operations=[{"op": "replace", "path": "/special_attributes/0/current_value", "value": 0}],
    )
    result = rewind(n, scene, target=count_one.data["operation"]["id"])
    assert result["present_count"] == 1 and present(scene) == [a.id]
    assert n.world[0].special_attributes[0].current_value == 1


async def test_old_scene_has_an_explicit_recording_start_and_rejects_untracked_or_frozen_changes():
    n, scene, a, _ = fixture()
    original_event = emit(scene, "enter", "启用记录前已入场", a.id)
    engine = runner(n)
    with pytest.raises(ValueError, match="旧内容未记录"):
        rewind(n, scene)
    await execute(engine, scene, "say_to_group", text="开始记录后的新内容")
    rewind(n, scene, target="start")
    assert [e.id for e in scene.events] == [original_event.id] and present(scene) == [a.id]
    assert rewind(n, scene, target="start")["position"] == 0
    await execute(engine, scene, "say_to_group", text="另一个续写")
    scene.confirmed = True
    frozen = n.model_dump_json()
    with pytest.raises(ValueError, match="冻结"):
        rewind(n, scene)
    assert n.model_dump_json() == frozen
    scene.confirmed = False
    n.world.append(Entity(name="未记录的外部修改"))
    before = capture_state(n, scene)
    with pytest.raises(ValueError, match="未记录的变更"):
        rewind(n, scene)
    assert capture_state(n, scene) == before


def test_failed_candidate_never_changes_live_fields_and_rewind_arguments_are_exclusive():
    n, scene, _, _ = fixture()
    before = capture_state(n, scene)

    def mutate(candidate, target):
        candidate.world.append(Entity(name="不应保存"))
        raise ValueError("后续校验失败")

    with pytest.raises(ValueError):
        record_change(n, scene, "edit_fields", "失败操作", mutate)
    assert capture_state(n, scene) == before and scene.timeline.operations == []
    with pytest.raises(ValidationError):
        RewindPerformance(steps=1, target="start")
    with pytest.raises(ValidationError):
        RewindPerformance(steps=0)
    assert "rewind_performance" in tools_for("gm")
    assert "rewind_performance" not in tools_for("role") and "rewind_performance" not in tools_for("creation")


async def test_fork_starts_a_new_recording_path_and_keeps_original_timeline_in_archive():
    n, scene, _, _ = fixture()
    await execute(runner(n), scene, "say_to_group", text="原路线")
    old_head = scene.timeline.head
    branched = fork(n, scene.entity.id)
    assert branched.timeline is None and branched.events == []
    assert n.branches[-1].scenes[-1].timeline.head == old_head
    await execute(runner(n), branched, "say_to_group", text="新路线")
    assert branched.timeline.operations[0].parent == ""


async def test_operation_targets_are_not_reused_after_whole_turn_restoration():
    n, scene, _, _ = fixture()
    await execute(runner(n), scene, "say_to_group", text="第一段")
    saved = n.model_dump_json()
    await execute(runner(n), scene, "say_to_group", text="第二段")
    removed_target = scene.timeline.head
    restored = StudioNovel.model_validate_json(saved)
    await execute(runner(restored), restored.scenes[-1], "say_to_group", text="第二段")
    assert restored.scenes[-1].timeline.head != removed_target


@pytest.fixture
async def client(studio_client):  # noqa: F811
    yield studio_client


async def test_api_rewind_persists_cursor_records_user_edits_and_excludes_private_log_payload(
    client, monkeypatch
):
    n = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{n['id']}"
    await client.post(path + "/message", json={"version": n["version"], "message": "灯塔故事"})

    async def get():
        return (await client.get(path)).json()

    async def post(endpoint, **data):
        current = await get()
        r = await client.post(path + endpoint, json={"version": current["version"], **data})
        assert r.status_code == 200, r.text
        return await get()

    await post("/control", action="confirm_creation")
    n = await post("/control", action="new_scene")
    scene_id = n["scenes"][-1]["entity"]["id"]
    n = await post("/edit", scope="characters", entity={"name": "甲"})
    role_id = n["characters"][0]["id"]
    n = await post("/control", action="enter", scene_id=scene_id, name=role_id)
    first_event = n["scenes"][-1]["events"][0]["id"]
    n = await post("/edit", scope="world", entity={"name": "临时设定", "type": "world"})
    original_world = n["world"][:-1]
    timeline = n["scenes"][-1]["timeline"]
    assert "base" not in timeline and "state_hash" not in timeline
    assert all("changes" not in op for op in timeline["operations"])
    assert {op["origin"] for op in timeline["operations"]} == {"user"}

    def actor(self, mode, data, history):
        if not any(m.tool_calls for m in history):
            return StudioStep(
                actions=[
                    StudioAction(name="rewind_performance", arguments={"target": "#1"}),
                    StudioAction(name="create_character", arguments={"name": "不应执行"}),
                ]
            )
        assert [call.function.name for m in history if m.tool_calls for call in m.tool_calls] == [
            "rewind_performance"
        ]
        assert [entity.model_dump(mode="json") for entity in data.novel.world] == original_world
        assert data.current_scene.rewind.position == 2 and len(data.current_scene.present) == 1
        return StudioAction(name="reply", text="已回到入场之后")

    monkeypatch.setattr(StudioRunner, "mock", actor)
    n = await post("/message", scene_id=scene_id, message="回到入场那段")
    assert n["world"] == original_world
    assert len(n["characters"]) == 1 and n["scenes"][-1]["messages"][-1]["content"] == "已回到入场之后"
    assert n["scenes"][-1]["timeline"]["head"] == timeline["operations"][1]["id"]
    preview = (await client.get(path + "/preview", params={"scene_id": scene_id})).json()
    rendered = preview["rendered_context"]
    assert rendered == preview["messages"][2]["content"]
    assert "当前位置第 2 步" in rendered and "在场 1 人" in rendered
    assert "#1" in rendered and "step:2" in rendered and first_event not in rendered
    assert "state_hash" not in rendered and '"changes"' not in rendered
    # Message-level withdrawal restores both the state and the operation cursor before that message.
    r = await client.post(
        path + "/turn",
        json={
            "version": n["version"],
            "message_id": n["turn_checkpoint"]["message_id"],
            "scene_id": scene_id,
            "action": "withdraw",
        },
    )
    assert r.status_code == 200, r.text
    restored = await get()
    assert restored["world"][-1]["name"] == "临时设定"
    assert restored["scenes"][-1]["timeline"]["head"] == timeline["head"]
