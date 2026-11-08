import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from ainovel.controller.api import create_app
from ainovel.core.config import Settings
from ainovel.core.entities import Attribute, Description, Entity, Visibility, visible_entity
from ainovel.core.gm_context import gm_context
from ainovel.core.studio import (
    ConversationMessage,
    Patch,
    StudioAction,
    StudioChapter,
    StudioNovel,
    StudioScene,
)
from ainovel.core.studio_state import (
    apply_patch,
    edit_self,
    emit,
    fork,
    present,
    require_editable,
    reset,
    role_context,
    role_states,
)
from ainovel.core.studio_tools import Edit, tools_for
from ainovel.service.studio import StudioRunner, build_request, edit_fields, estimate, writing_context


def test_creation_request_has_only_creation_tool_descriptions_and_parameters():
    from ainovel.core.studio_tools import StudioDecoder
    from ainovel.llm.errors import StructuredOutputError
    from ainovel.llm.schemas import ProviderMessage

    n = StudioNovel()
    request = build_request(
        Settings(_env_file=None),
        n,
        "creation",
        {"title": n.title, "world": [], "characters": [], "creation": []},
    )
    wire = request.body.model_dump_json()
    for stage_term in ("群聊", "入场", "在场", "GM", "演绎", "章节"):
        assert stage_term not in wire
    assert set(tools_for("creation")) == {"say_to_user", "edit_fields", "create_character"}
    with pytest.raises(ValidationError):
        tools_for("creation")["edit_fields"](scope="scenes", operations=[{"op": "remove", "path": "/0"}])
    with pytest.raises(StructuredOutputError):
        StudioDecoder("creation").decode(
            ProviderMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_1",
                        "function": {"name": "enter_character", "arguments": '{"character":"甲"}'},
                    }
                ],
            )
        )


async def test_creation_tool_results_do_not_report_performance_state():
    n = StudioNovel()
    runner = StudioRunner(n, None, Settings(_env_file=None))
    created = await runner.execute(
        "creation", None, StudioAction(name="create_character", arguments={"name": "甲"})
    )
    edited = await runner.execute(
        "creation",
        None,
        StudioAction(
            name="edit_fields",
            arguments={
                "scope": "creation",
                "operations": [{"op": "add", "path": "/-", "value": {"name": "文风", "type": "creation"}}],
            },
        ),
    )
    assert created.message == "人物资料已创建" and edited.message == "构思资料已更新"
    assert n.characters[0].name == "甲" and n.creation[0].name == "文风"
    gm_n, scene, _, _ = fixture()
    gm = await StudioRunner(gm_n, None, Settings(_env_file=None)).execute(
        "gm", scene, StudioAction(name="create_character", arguments={"name": "丙"})
    )
    assert "尚未入场" in gm.message


def fixture():
    a = Entity(
        name="甲",
        description=[
            Description(name="公开经历", value=["知道灯塔"]),
            Description(name="私密", value=["甲的秘密"], visibility=1),
            Description(name="系统", value=["绝不能泄露"], visibility=0),
        ],
        special_attributes=[Attribute(key="情绪", value_type="str", current_value="平静", visibility=1)],
    )
    b = Entity(name="乙", description=[Description(name="私密", value=["乙的秘密"], visibility=1)])
    chapter = StudioChapter(entity=Entity(name="第一章", type="chapter"))
    scene = StudioScene(entity=Entity(name="灯塔", type="scene"), chapter_id=chapter.entity.id)
    n = StudioNovel(created=True, characters=[a, b], chapters=[chapter], scenes=[scene])
    return n, scene, a, b


def test_description_and_attribute_visibility_and_types():
    n, s, a, b = fixture()
    assert [d.name for d in visible_entity(a, own=True).description] == ["公开经历", "私密"]
    assert [d.name for d in visible_entity(a, own=False).description] == ["公开经历"]
    assert Description(name="背景").visibility == Visibility.PUBLIC
    for value_type, current in [("int", True), ("float", True), ("bool", 1), ("list", "不是列表")]:
        with pytest.raises(ValidationError):
            Attribute(key="变量", value_type=value_type, current_value=current)
    with pytest.raises(ValidationError):
        Entity(name="重复", description=[Description(name="a"), Description(name="a")])


def test_visibility_tracks_entry_exit_intervals_and_no_other_novel_data():
    n, s, a, b = fixture()
    n.world = [Entity(name="幕后世界秘密")]
    emit(s, "gm", "加入前的信息")
    emit(s, "enter", "甲到场", a.id)
    emit(s, "enter", "乙到场", b.id)
    emit(s, "gm", "一起看到的灯光")
    emit(s, "leave", "甲离开", a.id)
    emit(s, "gm", "甲不该知道的交易")
    emit(s, "enter", "甲返回", a.id)
    emit(s, "gm", "甲返回后的消息")
    data = role_context(n, s, a.id)
    text = json.dumps(data, ensure_ascii=False)
    for secret in ("加入前的信息", "甲不该知道的交易", "乙的秘密", "绝不能泄露", "幕后世界秘密"):
        assert secret not in text
    assert "甲的秘密" in text and "一起看到的灯光" in text and "甲返回后的消息" in text
    assert present(s) == [b.id, a.id]
    emit(s, "leave", "甲再离开", a.id)
    with pytest.raises(ValueError, match="不在场"):
        role_context(n, s, a.id)


def test_snapshot_replay_and_branch_preserve_original_route():
    n, s, a, b = fixture()
    s.character_patches[a.id] = [
        Patch(op="replace", path="/special_attributes/0/current_value", value="紧张")
    ]
    s.confirmed = True
    s.character_snapshot = role_states(n, s)
    later = StudioScene(entity=Entity(name="第二幕", type="scene"), chapter_id=s.chapter_id)
    n.scenes.append(later)
    later.character_patches[a.id] = [
        Patch(op="replace", path="/special_attributes/0/current_value", value="安心")
    ]
    assert role_states(n, later)[a.id].special_attributes[0].current_value == "安心"
    assert role_states(n, s)[a.id].special_attributes[0].current_value == "紧张"
    with pytest.raises(ValueError, match="冻结"):
        require_editable(n, s)
    branched = fork(n, s.entity.id)
    assert len(n.scenes) == 1 and not branched.confirmed
    assert len(n.branches[0].scenes) == 2
    assert n.branches[0].scenes[0].character_snapshot[a.id].special_attributes[0].current_value == "紧张"
    assert role_states(n, branched)[a.id].special_attributes[0].current_value == "平静"


def test_reset_preserves_settings_and_clears_only_rehearsal():
    n, s, a, b = fixture()
    edit_fields(
        n,
        s,
        Edit(
            scope="characters",
            target="甲",
            operations=[Patch(op="replace", path="/special_attributes/0/current_value", value="警惕")],
        ),
    )
    emit(s, "enter", "甲到场", a.id)
    edit_self(n, s, a.id, [Patch(op="replace", path="/special_attributes/0/current_value", value="生气")])
    n.world.append(Entity(name="调整后的世界观"))
    assert role_states(n, s)[a.id].special_attributes[0].current_value == "生气"
    reset(s)
    assert present(s) == [] and s.character_patches == {} and len(s.abandoned) == 1
    assert n.world[0].name == "调整后的世界观"
    assert role_states(n, s)[a.id].special_attributes[0].current_value == "警惕"
    emit(s, "enter", "重新到场", a.id)
    assert "生气" not in json.dumps(role_context(n, s, a.id), ensure_ascii=False)


def test_self_patch_preserves_hidden_fields_and_cannot_promote_to_system():
    n, s, a, b = fixture()
    edit_self(n, s, a.id, [Patch(op="replace", path="/description/0/value", value=["新的公开经历"])])
    after = role_states(n, s)[a.id]
    assert next(d for d in after.description if d.visibility == 0).value == ["绝不能泄露"]
    with pytest.raises(ValueError):
        edit_self(n, s, a.id, [Patch(op="replace", path="/name", value="乙")])
    with pytest.raises(ValueError):
        edit_self(n, s, a.id, [Patch(op="replace", path="/description/0/visibility", value=0)])
    assert b.description[0].value == ["乙的秘密"]


async def test_named_field_edits_and_scene_local_character_names():
    novel, scene, character, _ = fixture()
    change_mood = False

    async def save(_):
        pass

    async def actor(mode, data, history, request):
        assert data["self"]["name"] == "清妍"
        if change_mood and not history:
            return StudioAction(
                name="edit_self",
                arguments={"operations": [{"op": "set_field", "field": "情绪", "value": "安心"}]},
            )
        return StudioAction(name="reply", text="我在。")

    runner = StudioRunner(novel, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor)

    async def gm(name, **arguments):
        return await runner.execute("gm", scene, StudioAction(name=name, arguments=arguments))

    await gm(
        "edit_fields",
        scope="characters",
        target="甲",
        operations=[
            {"op": "replace", "path": "/name", "value": "清妍"},
            {"op": "remove_field", "field": "公开经历"},
            {"op": "set_field", "field": "情绪", "value": "警惕"},
        ],
    )
    await gm("enter_character", character="清妍")
    result = await gm("schedule_characters", characters=["清妍"])
    assert result.data["replies"] == [{"character": "清妍", "reply": "我在。"}]
    assert scene.settings_patches[character.id][-1].path == "/special_attributes/0/current_value"
    assert role_states(novel, scene)[character.id].special_attributes[0].current_value == "警惕"

    edits = tools_for("role")["edit_self"]
    with pytest.raises(ValueError, match="找不到可编辑字段"):
        edit_self(
            novel,
            scene,
            character.id,
            edits(
                operations=[
                    {"op": "set_field", "field": "情绪", "value": "紧张"},
                    {"op": "remove_field", "field": "系统"},
                ]
            ).operations,
        )
    assert role_states(novel, scene)[character.id].special_attributes[0].current_value == "警惕"
    change_mood = True
    await gm("schedule_characters", characters=["清妍"])
    assert role_states(novel, scene)[character.id].special_attributes[0].current_value == "安心"
    await gm("remove_character", character="清妍")


def test_patch_atomicity_and_pointer_escaping():
    doc = {"a/b": {"~key": [1, 2]}}
    assert apply_patch(doc, [Patch(op="add", path="/a~1b/~0key/1", value=3)]) == {"a/b": {"~key": [1, 3, 2]}}
    with pytest.raises(ValueError):
        apply_patch(
            doc, [Patch(op="remove", path="/a~1b/~0key/0"), Patch(op="replace", path="/missing", value=1)]
        )
    assert doc == {"a/b": {"~key": [1, 2]}}
    for path in ("/a~1b/~0key/-1", "/a~1b/~0key/01", "/a~9b"):
        with pytest.raises(ValueError):
            apply_patch(doc, [Patch(op="remove", path=path)])


async def test_gm_sends_user_messages_without_yielding_and_roles_return_to_gm():
    n, s, a, b = fixture()
    n.run_id = str(uuid4())
    n.busy = True
    emit(s, "enter", "甲到场", a.id)
    emit(s, "enter", "乙到场", b.id)
    seen = []
    gm = [
        StudioAction(name="say_to_user", arguments={"text": "我会先观察角色如何决定。"}),
        StudioAction(
            name="schedule_characters",
            arguments={"characters": [a.id, b.id]},
        ),
        StudioAction(name="say_to_group", arguments={"text": "两人的尝试让灯塔守卫注意到了他们。"}),
        StudioAction(name="reply", text="请检查演绎"),
    ]

    async def actor(mode, data, history, request):
        seen.append((mode, data))
        if mode == "gm":
            return gm.pop(0)
        return StudioAction(name="reply", text=data["self"]["name"] + "决定等待")

    async def save(novel):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", s)
    assert [mode for mode, _ in seen] == ["gm", "gm", "role", "role", "gm", "gm"]
    assert s.messages[0].content == "我会先观察角色如何决定。"
    assert s.messages[-1].content == "请检查演绎"
    assert not n.busy
    for mode, data in seen:
        if mode == "role":
            assert "我会先观察" not in json.dumps(data, ensure_ascii=False)
    assert [e.kind for e in s.events[-3:]] == ["role", "role", "gm"]


async def test_budget_includes_nested_roles_and_preserves_progress():
    n, s, a, b = fixture()
    n.run_id = str(uuid4())
    n.compression.max_calls = 2
    emit(s, "enter", "甲到场", a.id)

    async def actor(mode, data, history, request):
        return (
            StudioAction(name="schedule_characters", arguments={"characters": [a.id]})
            if mode == "gm"
            else StudioAction(name="reply", text="甲决定等待")
        )

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", s)
    assert n.calls == 2 and not n.busy
    assert "预算" in s.messages[-1].content
    assert s.events[-1].text == "甲决定等待"


def test_schemas_are_small_and_context_jinja_cannot_access_hidden_fields():
    assert len(tools_for("gm")) == 9 and set(tools_for("role")) == {"edit_self", "leave"}
    assert "id" not in tools_for("gm")["create_character"].model_json_schema()["properties"]
    n, s, a, b = fixture()
    emit(s, "enter", "甲到场", a.id)
    request = build_request(Settings(_env_file=None), n, "role", role_context(n, s, a.id))
    wire = request.body.model_dump_json()
    assert "绝不能泄露" not in wire
    assert estimate(request)["input_tokens"] > 0
    writing = build_request(Settings(_env_file=None), n, "writing", {"events": []})
    assert writing.body.tools is None and writing.body.tool_choice is None


def test_studio_context_keeps_chinese_in_the_message_content():
    data = {"scene": "第 1 幕", "text": "清妍说：“你好。”\n\n她笑了。"}
    request = build_request(Settings(_env_file=None), StudioNovel(), "gm", data)
    content = request.body.messages[-1].content
    assert "第 1 幕" in content and "清妍" in content
    assert "\\u7b2c" not in content
    assert json.loads(content) == data
    assert json.loads(request.body.model_dump_json())["messages"][-1]["content"] == content


@pytest.mark.parametrize(
    ("writing_mode", "requirements", "expected_task"),
    [
        ("generate", "", "首次生成本幕正文"),
        ("rewrite", "  \n", "根据 events 从头重写整幕"),
        ("rewrite", "把独白改短", "优先落实本次修改要求"),
    ],
)
def test_writing_prompt_selects_task_from_mode_and_requirements(writing_mode, requirements, expected_task):
    n = StudioNovel()
    data = {"writing_mode": writing_mode, "requirements": requirements}
    request = build_request(Settings(_env_file=None), n, "writing", data)
    prompt = request.body.messages[0].content
    assert expected_task in prompt
    assert "正文第一段直接写开场情境或开场动作" in prompt
    assert ("旧稿只供参考" in prompt) == (writing_mode == "rewrite" and not requirements.strip())


async def test_estimate_does_not_save_edits_and_invalid_jinja_is_readable(client):
    n = (await client.post("/api/studio/novels", json={})).json()
    key = n["id"]
    candidate = {
        "scene_id": "",
        "compression": n["compression"],
        "prompts": {},
        "context_source": "{{ data.title }}",
        "message": "新要求",
    }
    preview = await client.post(f"/api/studio/novels/{key}/estimate", json=candidate)
    assert preview.status_code == 200, preview.text
    assert preview.json()["messages"][-1]["content"] == "新要求"
    after = (await client.get("/api/studio/novels/" + key)).json()
    assert after["version"] == n["version"] and after["context_source"] == n["context_source"]
    candidate["context_source"] = "{{ invalid_variable }}"
    invalid = await client.post(f"/api/studio/novels/{key}/estimate", json=candidate)
    assert invalid.status_code == 400 and "Jinja" in invalid.json()["detail"]


async def test_gm_template_preview_and_real_call_share_rendering(client, monkeypatch):
    from ainovel.service import studio

    n = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{n['id']}"

    async def post(endpoint, **payload):
        current = (await client.get(path)).json()
        response = await client.post(path + endpoint, json={"version": current["version"], **payload})
        assert response.status_code == 200, response.text
        return (await client.get(path)).json()

    await post("/message", message="灯塔故事")
    await post("/control", action="confirm_creation")
    current = await post("/control", action="new_scene", name="海边初遇")
    scene_id = current["scenes"][0]["entity"]["id"]
    await post("/message", scene_id=scene_id, message="讨论旧方案")
    draft = "这次改用新方案"
    preview = (await client.get(path + "/preview", params={"scene_id": scene_id, "message": draft})).json()
    assert "## 当前幕" in preview["rendered_context"] and "讨论旧方案" in preview["rendered_context"]
    assert draft not in preview["rendered_context"] and preview["messages"][-1]["content"] == draft

    source = "{{ data.current_scene.settings.name }} / {{ data.discussion[0].text }}"
    payload = {
        "compression": current["compression"],
        "prompts": {},
        "context_source": "{{ data.title }}",
        "gm_context_source": source,
    }
    estimate = await client.post(path + "/estimate", json={**payload, "scene_id": scene_id, "message": draft})
    assert estimate.status_code == 200 and estimate.json()["rendered_context"] == "海边初遇 / 讨论旧方案"
    assert (await client.get(path)).json()["gm_context_source"] is None
    saved = await post("/settings", **payload)
    assert saved["gm_context_source"] == source

    seen = []
    build = studio.build_request

    def record(*args, **kwargs):
        request = build(*args, **kwargs)
        seen.append([message.model_dump(mode="json") for message in request.body.messages])
        return request

    monkeypatch.setattr(studio, "build_request", record)
    await post("/message", scene_id=scene_id, message=draft)
    assert seen[0] == estimate.json()["messages"]


def test_gm_legacy_layout_preserves_old_data_paths_and_native_history():
    from ainovel.service.studio import gm_turn_messages

    novel, scene, _, _ = fixture()
    scene.messages = [ConversationMessage(role="user", content="早先的讨论")]
    discussion, history = gm_turn_messages(scene.messages, "本轮要求")
    novel.context_source = "{{ data.title }} / {{ data.current_scene.scene.name }}"
    novel.prompts = {"gm": "{{ data.present_count }} 人"}
    request = build_request(
        Settings(_env_file=None), novel, "gm", gm_context(novel, scene, discussion), history
    )
    assert [message.content for message in request.body.messages[2:]] == [
        f"{novel.title} / {scene.entity.name}",
        "早先的讨论",
        "本轮要求",
    ]
    assert request.body.messages[0].content == "0 人"


async def test_gm_template_library_keeps_selection_and_preview_separate(client, monkeypatch):
    from ainovel.service import studio

    novel = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{novel['id']}"
    source = "当前幕 {{ data.current_scene.settings.name }}"

    async def update(endpoint, **data):
        nonlocal novel
        response = await client.post(path + endpoint, json={"version": novel["version"], **data})
        assert response.status_code == 200, response.text
        novel = (await client.get(path)).json()

    response = await client.post(path + "/gm-template-preview", json={"source": source})
    assert response.json()["rendered_context"] == "当前幕 示例幕"
    assert response.json()["sample"] is True
    assert (await client.get(path)).json() == novel

    await update("/gm-templates", name="短版", source=source)
    template_id = novel["gm_templates"][0]["id"]
    assert novel["active_gm_template_id"] is None
    await update("/gm-template-selection", id=template_id)
    await update("/message", message="一部灯塔小说")
    await update("/control", action="confirm_creation")
    await update("/control", action="new_scene", name="初遇")
    scene_id = novel["scenes"][0]["entity"]["id"]

    async def rendered():
        return (await client.get(path + "/preview", params={"scene_id": scene_id})).json()["rendered_context"]

    assert await rendered() == "当前幕 初遇"
    await update(
        "/settings",
        compression=novel["compression"],
        prompts={},
        context_source=novel["context_source"],
        gm_context_source=None,
    )
    assert novel["active_gm_template_id"] == template_id
    await update("/gm-templates", id=template_id, name="新版", source="新版 " + source)
    assert await rendered() == "当前幕 初遇"
    response = await client.post(path + "/gm-template-preview", json={"source": "临时", "scene_id": scene_id})
    assert response.json()["rendered_context"] == "临时"
    assert response.json()["active"]["rendered_context"] == "当前幕 初遇"
    assert response.json()["context_tokens"] < response.json()["active"]["context_tokens"]

    await update("/message", scene_id=scene_id, message="讨论本幕")
    message_id = novel["turn_checkpoint"]["message_id"]
    await update("/gm-template-selection", id=template_id)
    assert await rendered() == "新版 当前幕 初遇"
    assert novel["turn_checkpoint"]["version"] == novel["version"]
    seen = []

    def record(*args, **kwargs):
        request = build_request(*args, **kwargs)
        seen.append(request.body.messages[2].content)
        return request

    monkeypatch.setattr(studio, "build_request", record)
    await update("/turn", action="retry", scene_id=scene_id, message_id=message_id)
    assert seen and all(context == "新版 当前幕 初遇" for context in seen)
    assert len(novel["scenes"][0]["events"]) == 1

    await update("/edit", scope="world", title="手动修改后的书名")
    await update("/gm-templates", name="另存草稿", source=source)
    assert novel["turn_checkpoint"]["version"] != novel["version"]
    blocked = await client.post(path + "/turn", json={
        "version": novel["version"], "action": "retry", "scene_id": scene_id,
        "message_id": novel["turn_checkpoint"]["message_id"],
    })
    assert blocked.status_code == 400, blocked.text

    await update("/gm-template-selection", id="default")
    assert "## 当前幕" in await rendered()


async def test_shared_gm_template_copy_keeps_its_variables(client):
    novel = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{novel['id']}"
    source = "{{ data.title }} / {{ data.current_scene.scene.name }}"
    preview = await client.post(path + "/gm-template-preview", json={"source": source, "schema_version": "shared-v1"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["rendered_context"] == "未命名小说 / 示例幕"

    saved = await client.post(path + "/gm-templates", json={
        "version": novel["version"], "name": "共享模板副本", "source": source, "schema_version": "shared-v1",
    })
    assert saved.status_code == 200, saved.text
    novel = saved.json()
    template_id = novel["gm_templates"][0]["id"]
    activated = await client.post(path + "/gm-template-selection", json={"version": novel["version"], "id": template_id})
    assert activated.status_code == 200, activated.text
    assert activated.json()["active_gm_template"]["schema_version"] == "shared-v1"

    novel = activated.json()
    saved = await client.post(path + "/gm-templates", json={
        "version": novel["version"], "id": template_id, "name": "草稿", "source": "{{ missing_variable }}",
    })
    assert saved.status_code == 200, saved.text
    blocked = await client.post(path + "/gm-template-selection", json={
        "version": saved.json()["version"], "id": template_id,
    })
    assert blocked.status_code == 400, blocked.text
    assert (await client.get(path)).json()["active_gm_template"]["source"] == source


@pytest.mark.parametrize(("mode", "source", "rendered"), [
    ("role", "{{ data.self.name }} / {{ data.direction }}", "示例角色 / 试用要求"),
    ("writing", "{{ data.scene.name }} / {{ data.requirements }}", "示例幕 / 试用要求"),
])
async def test_context_templates_have_immutable_defaults_and_independent_copies(client, mode, source, rendered):
    novel = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{novel['id']}"

    async def post(endpoint, **data):
        return await client.post(path + endpoint, json={"version": novel["version"], "mode": mode, **data})

    blocked = await post("/context-templates", id="default", name="改内置", source=source)
    assert blocked.status_code == 400, blocked.text
    preview = await client.post(path + "/context-template-preview", json={
        "mode": mode, "source": source, "message": "试用要求",
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["rendered_context"] == rendered and preview.json()["sample"]
    saved = await post("/context-templates", name="自定义副本", source=source)
    assert saved.status_code == 200, saved.text
    novel = saved.json()
    template = novel["context_template_sets"][mode]["templates"][0]
    enabled = await post("/context-template-selection", id=template["id"], message="试用要求")
    assert enabled.status_code == 200, enabled.text
    novel = enabled.json()
    assert novel["context_template_sets"][mode]["active"] == template
    assert novel["active_gm_template_id"] is None
    restored = await post("/context-template-selection", id="default")
    assert restored.status_code == 200, restored.text
    state = restored.json()["context_template_sets"][mode]
    assert state["active_id"] == "default" and state["active"] is None and state["templates"] == [template]


def test_gm_default_template_separates_current_names_from_historical_quotes():
    novel, scene, character, _ = fixture()
    character.name = "清妍"
    emit(scene, "enter", "走进房间", character.id)
    emit(scene, "role", "以前有人叫我清晏。", character.id)
    request = build_request(Settings(_env_file=None), novel, "gm", gm_context(novel, scene))
    rendered = request.body.messages[2].content
    assert rendered.index("## 当前角色名册") < rendered.index("## 当前幕")
    assert "### 清妍" in rendered and "角色发言或行动 · 清妍：以前有人叫我清晏。" in rendered
    assert request.body.tools


def test_default_gm_and_writing_use_the_selected_chapter_and_keep_legacy_scene_fields_available():
    novel, scene, _, _ = fixture()
    novel.chapters[0].entity.description = [Description(name="章目标", value=["找到密信"])]
    novel.chapters.append(StudioChapter(entity=Entity(name="下一章")))
    scene.entity.description = [Description(name="旧幕字段", value=["旧幕里保存的资料"])]
    settings = Settings(_env_file=None)

    gm_data = gm_context(novel, scene)
    writing_data = writing_context(novel, scene)
    gm_request = build_request(settings, novel, "gm", gm_data)
    writing_request = build_request(settings, novel, "writing", writing_data)
    for request in (gm_request, writing_request):
        rendered = request.body.messages[2].content
        assert "找到密信" in rendered
        assert "下一章" not in rendered and "旧幕里保存的资料" not in rendered

    novel.context_source = "{{ data.scene.description[0].value[0] }}"
    assert build_request(settings, novel, "writing", writing_data).body.messages[2].content == "旧幕里保存的资料"


async def test_studio_custom_documentation_is_previewed_and_sent_with_native_tools(client, monkeypatch):
    from ainovel.service import studio

    novel = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{novel['id']}"
    docs = {"gm": {
        "tool_descriptions": {"say_to_group": "只广播角色当前感知到的客观结果。"},
        "variable_descriptions": {"data.novel.title": "作品正式名称，旧称可能出现在历史里。"},
    }}
    source = "{{ data.novel.title }}"
    draft = await client.post(path + "/context-template-preview", json={"source": source, "documentation": docs})
    assert draft.status_code == 200, draft.text
    preview = draft.json()
    assert preview["documentation"]["tool_descriptions"]["say_to_group"] == docs["gm"]["tool_descriptions"]["say_to_group"]
    assert preview["documentation"]["variable_schema"]["data.novel.title"] == {
        "type": "string", "description": docs["gm"]["variable_descriptions"]["data.novel.title"],
    }
    assert "data.novel.title" not in preview["active"]["documentation"]["variable_schema"]
    assert (await client.get(path)).json()["documentation"] == {}

    settings = {key: novel[key] for key in ("version", "compression", "prompts", "context_source")}
    rejected = await client.post(path + "/settings", json={**settings, "documentation": {
        "role": {"tool_descriptions": {"say_to_group": "不可借此增加角色权限"}},
    }})
    assert rejected.status_code == 400, rejected.text
    saved = await client.post(path + "/settings", json={**settings, "documentation": docs})
    assert saved.status_code == 200, saved.text
    catalog = (await client.get("/api/studio/contracts")).json()
    default_tool = next(tool for tool in catalog["tools"]["gm"] if tool["name"] == "say_to_group")
    seen = []

    def record(*args, **kwargs):
        request = build_request(*args, **kwargs)
        if request.metadata.node == "gm":
            seen.append(request.body)
        return request

    monkeypatch.setattr(studio, "build_request", record)

    async def update(endpoint, **data):
        current = (await client.get(path)).json()
        response = await client.post(path + endpoint, json={"version": current["version"], **data})
        assert response.status_code == 200, response.text
        return (await client.get(path)).json()

    await update("/message", message="写灯塔故事")
    await update("/control", action="confirm_creation")
    current = await update("/control", action="new_scene")
    await update("/message", scene_id=current["scenes"][0]["entity"]["id"], message="看看海面")
    assert seen
    assert json.loads(seen[0].messages[1].content) == preview["documentation"]
    native = next(tool.function.model_dump(mode="json") for tool in seen[0].tools if tool.function.name == "say_to_group")
    assert native["description"] == docs["gm"]["tool_descriptions"]["say_to_group"]
    assert native["parameters"] == default_tool["parameters"]


async def test_api_rejects_user_messages_and_edits_during_gm_execution(client, monkeypatch):
    import asyncio

    started, resume = asyncio.Event(), asyncio.Event()

    async def paused_call(self, mode, data, history):
        started.set()
        await resume.wait()
        return StudioAction(name="reply", text="完成")

    monkeypatch.setattr(StudioRunner, "call", paused_call)
    n = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{n['id']}"
    task = asyncio.create_task(
        client.post(path + "/message", json={"version": n["version"], "message": "开始"})
    )
    try:
        await asyncio.wait_for(started.wait(), 3)
        current = (await client.get(path)).json()
        assert current["busy"]
        blocked = await client.post(
            path + "/message", json={"version": current["version"], "message": "不应插入"}
        )
        assert blocked.status_code == 409
        blocked_edit = await client.post(
            path + "/edit", json={"version": current["version"], "scope": "world", "title": "不应修改"}
        )
        assert blocked_edit.status_code == 409
    finally:
        resume.set()
        await asyncio.wait_for(task, 3)


def test_chapter_compression_falls_back_without_truncating():
    n, s, a, b = fixture()
    emit(s, "gm", "很长的消息" * 2000)
    s.confirmed = True
    second = StudioChapter(entity=Entity(name="第二章", type="chapter"))
    current = StudioScene(entity=Entity(name="新幕", type="scene"), chapter_id=second.entity.id)
    n.chapters.append(second)
    n.scenes.append(current)
    n.compression.full_chapters = 0
    n.compression.scene_chapters = 0
    assert gm_context(n, current).chapters[0].scenes[0].performance[0].text == "很长的消息" * 2000
    n.chapters[0].summary = "灯塔事件"
    n.chapters[0].summary_stale = False
    chapter = gm_context(n, current).chapters[0]
    assert chapter.summary == "灯塔事件" and chapter.scenes == []
    n.compression.scene_chapters = 2
    s.text = "已经写好的正文"
    chapter = gm_context(n, current).chapters[0]
    assert chapter.level == "scenes" and chapter.summary is None and chapter.scenes[0].prose is None
    assert chapter.scenes[0].performance[0].text == "很长的消息" * 2000


@pytest.fixture
async def client(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            llm_provider="mock",
            database_url="sqlite+aiosqlite:///" + str(tmp_path / "studio.db"),
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        yield client


async def test_api_gm_answers_user_question_without_tool_calls_and_keeps_it_after_reload(client, monkeypatch):
    n = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{n['id']}"
    await client.post(path + "/message", json={"version": n["version"], "message": "构思一个灯塔故事"})
    n = (await client.get(path)).json()
    await client.post(path + "/control", json={"version": n["version"], "action": "confirm_creation"})
    n = (await client.get(path)).json()
    await client.post(path + "/control", json={"version": n["version"], "action": "new_scene"})
    n = (await client.get(path)).json()
    scene_id = n["scenes"][-1]["entity"]["id"]
    before_calls = n["calls"]
    original = StudioRunner.mock
    reply = "可以把冲突设为信任问题，先讨论动机，不必立即演绎。"

    def mock(self, mode, data, history):
        if mode == "gm":
            return StudioAction(
                name="reply",
                text=json.dumps({"channel": "user_discussion", "speaker": "gm", "text": reply}),
            )
        return original(self, mode, data, history)

    monkeypatch.setattr(StudioRunner, "mock", mock)
    r = await client.post(
        path + "/message",
        json={"version": n["version"], "scene_id": scene_id, "message": "这幕的冲突可以怎么安排？"},
    )
    assert r.status_code == 200, r.text
    after = (await client.get(path)).json()
    messages = after["scenes"][-1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == reply
    assert after["calls"] == before_calls + 1 and not after["busy"] and not after["error"]
    assert after["scenes"][-1]["events"] == []
    assert after["turn_checkpoint"]["version"] == after["version"]
    again = (await client.get(path)).json()
    assert again["scenes"][-1]["messages"] == messages


async def test_api_progressive_creation_performance_prose_branch_and_purge(client, monkeypatch):
    from ainovel.service import studio

    n = (await client.post("/api/studio/novels", json={})).json()
    key = n["id"]

    async def get():
        return (await client.get("/api/studio/novels/" + key)).json()

    async def control(action, **extra):
        n = await get()
        response = await client.post(
            f"/api/studio/novels/{key}/control", json={"version": n["version"], "action": action, **extra}
        )
        assert response.status_code == 200, response.text
        return await get()

    async def enable_template(mode, source, **preview_input):
        path = f"/api/studio/novels/{key}"
        current = await get()
        saved = await client.post(path + "/context-templates", json={
            "version": current["version"], "mode": mode, "name": "测试副本", "source": source,
        })
        assert saved.status_code == 200, saved.text
        current = saved.json()
        item = current["context_template_sets"][mode]["templates"][0]
        activated = await client.post(path + "/context-template-selection", json={
            "version": current["version"], "mode": mode, "id": item["id"], **preview_input,
        })
        assert activated.status_code == 200, activated.text
        return activated.json()

    rendered = {"role": [], "writing": []}

    def record(settings, novel, mode, data, *args, **kwargs):
        request = build_request(settings, novel, mode, data, *args, **kwargs)
        if mode in rendered:
            rendered[mode].append(request.body.messages[2].content)
        return request

    monkeypatch.setattr(studio, "build_request", record)

    response = await client.post(
        f"/api/studio/novels/{key}/message", json={"version": n["version"], "message": "海上的灯塔都市"}
    )
    assert response.status_code == 200, response.text
    n = await get()
    assert n["world"][0]["description"][0]["value"] == ["海上的灯塔都市"] and not n["created"]
    response = await client.post(
        f"/api/studio/novels/{key}/edit",
        json={
            "version": n["version"],
            "scope": "characters",
            "entity": {
                "name": "甲",
                "description": [{"name": "秘密", "value": ["隐藏动机"], "visibility": 1}],
            },
        },
    )
    assert response.status_code == 200, response.text
    n = await control("confirm_creation")
    n = await control("new_scene")
    scene_id = n["scenes"][0]["entity"]["id"]
    n = await control("enter", scene_id=scene_id, name="甲")
    n = await enable_template("role", "角色 {{ data.self.name }} / {{ data.self.description[0].value[0] }}", scene_id=scene_id)
    response = await client.post(
        f"/api/studio/novels/{key}/message",
        json={"version": n["version"], "scene_id": scene_id, "message": "让甲自己决定是否进入灯塔"},
    )
    assert response.status_code == 200, response.text
    n = await get()
    assert any(e["kind"] == "role" for e in n["scenes"][0]["events"])
    assert rendered["role"] == ["角色 甲 / 隐藏动机"]
    n = await control("confirm_scene", scene_id=scene_id)
    await enable_template("writing", "{{ data.writing_mode }} / {{ data.requirements }}", scene_id=scene_id)
    original_mock = StudioRunner.mock
    writing_requests = []
    replies = iter(["第一版整幕正文", " ", "第二版整幕正文"])

    def mock(self, mode, data, history):
        if mode != "writing":
            return original_mock(self, mode, data, history)
        assert not history
        writing_requests.append(data)
        return StudioAction(name="reply", text=next(replies))

    monkeypatch.setattr(StudioRunner, "mock", mock)
    n = await control("write", scene_id=scene_id, instruction="使用第一人称")
    assert n["scenes"][0]["text"] == "第一版整幕正文"
    assert n["scenes"][0]["writing_requirements"] == "使用第一人称"
    n = await control("write", scene_id=scene_id, instruction="补充环境细节")
    assert n["error"] == "模型没有返回正文"
    assert n["scenes"][0]["text"] == "第一版整幕正文"
    assert n["scenes"][0]["writing_requirements"] == "使用第一人称"
    n = await control("write", scene_id=scene_id, instruction="缩短对话")
    assert n["scenes"][0]["text"] == "第二版整幕正文"
    assert n["scenes"][0]["writing_requirements"] == "缩短对话"
    assert [
        (data["writing_mode"], data["text"], data["previous_requirements"], data["requirements"])
        for data in writing_requests
    ] == [
        ("generate", "", "", "使用第一人称"),
        ("rewrite", "第一版整幕正文", "使用第一人称", "补充环境细节"),
        ("rewrite", "第一版整幕正文", "使用第一人称", "缩短对话"),
    ]
    assert all(data["events"] == writing_requests[0]["events"] for data in writing_requests)
    assert rendered["writing"] == ["generate / 使用第一人称", "rewrite / 补充环境细节", "rewrite / 缩短对话"]
    n = await control("finish_chapter", scene_id=scene_id)
    assert n["chapters"][0]["summary"] and not n["chapters"][0]["summary_stale"]
    n = await control("new_scene", new_chapter=True)
    locked = await client.post(
        f"/api/studio/novels/{key}/message",
        json={"version": n["version"], "scene_id": scene_id, "message": "修改旧幕"},
    )
    assert locked.status_code == 400 and "冻结" in locked.text
    n = await control("branch", scene_id=scene_id)
    assert len(n["scenes"]) == 1 and len(n["branches"][0]["scenes"]) == 2
    assert not n["scenes"][0]["confirmed"] and n["scenes"][0]["text"] == ""
    assert n["scenes"][0]["writing_requirements"] == ""
    assert n["branches"][0]["scenes"][0]["writing_requirements"] == "缩短对话"
    stale = await client.post(f"/api/studio/novels/{key}/control", json={"version": 0, "action": "delete"})
    assert stale.status_code == 409
    n = await control("delete")
    assert (
        await client.delete(f"/api/studio/novels/{key}", params={"version": n["version"]})
    ).status_code == 200
    assert (await client.get("/api/studio/novels")).json() == []


async def test_creation_requires_conversation_before_confirming(client):
    novel = (await client.post("/api/studio/novels", json={})).json()
    path = "/api/studio/novels/" + novel["id"]
    blocked = await client.post(
        path + "/control", json={"version": novel["version"], "action": "confirm_creation"}
    )
    assert blocked.status_code == 400 and "AI" in blocked.json()["detail"]
    assert not (await client.get(path)).json()["created"]


async def test_templates_initialize_independent_novel_fields_and_export_as_new(client):
    from ainovel.core.studio_templates import TemplateFields

    fields = TemplateFields().model_dump(mode="json")
    fields["world"]["description"] = [{"name": "世界规则", "value": ["模板初始世界"], "visibility": 2}]
    fields["characters"]["description"] = [{"name": "动机", "value": ["模板动机"], "visibility": 1}]
    fields["chapters"]["description"] = [{"name": "章节目标", "value": [], "visibility": 2}]
    fields["scenes"] = {
        "name": "旧幕模板", "type": "scene", "description": [{"name": "气氛", "value": ["安静"]}],
    }
    response = await client.post("/api/studio/templates", json={"name": "人物驱动", "fields": fields})
    assert response.status_code == 200, response.text
    template = response.json()
    assert "scenes" not in template["fields"]
    novel = (await client.post("/api/studio/novels", json={})).json()
    path = "/api/studio/novels/" + novel["id"]
    applied = await client.post(
        path + "/template", json={"version": novel["version"], "template_id": template["id"]}
    )
    assert applied.status_code == 200, applied.text
    novel = applied.json()
    assert novel["world"][0]["description"][0]["value"] == ["模板初始世界"]
    assert not novel["created"]
    world = novel["world"][0]
    world["description"][0]["value"] = ["本书自己的世界"]
    response = await client.post(
        path + "/edit",
        json={
            "version": novel["version"],
            "scope": "world",
            "target": world["id"],
            "entity": {k: v for k, v in world.items() if k != "id"},
        },
    )
    assert response.status_code == 200, response.text
    novel = response.json()
    character = await client.post(
        path + "/edit", json={"version": novel["version"], "scope": "characters", "entity": {"name": "甲"}}
    )
    assert character.status_code == 200, character.text
    novel = character.json()
    assert novel["characters"][0]["description"][0]["visibility"] == 1
    assert novel["characters"][0]["description"][0]["value"] == ["模板动机"]
    exported = await client.post(
        path + "/template/export", json={"version": novel["version"], "name": "本书新模板"}
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["id"] != template["id"]
    assert exported.json()["fields"]["world"]["description"][0]["value"] == ["本书自己的世界"]
    library = (await client.get("/api/studio/templates")).json()
    assert next(t for t in library if t["id"] == template["id"])["fields"]["world"]["description"][0][
        "value"
    ] == ["模板初始世界"]
    fields["world"]["description"][0]["value"] = ["修改后的全局模板"]
    updated = await client.post(
        "/api/studio/templates",
        json={
            "id": template["id"],
            "version": template["version"],
            "name": "直接编辑后的模板",
            "fields": fields,
        },
    )
    assert updated.status_code == 200, updated.text
    assert (await client.get(path)).json()["world"][0]["description"][0]["value"] == ["本书自己的世界"]
    stale = await client.post(
        "/api/studio/templates",
        json={"id": template["id"], "version": template["version"], "name": "过期保存", "fields": fields},
    )
    assert stale.status_code == 409
    await client.post(
        path + "/message",
        json={"version": novel["version"], "message": "这本小说已经有世界和角色，请继续整理"},
    )
    novel = (await client.get(path)).json()
    response = await client.post(
        path + "/control", json={"version": novel["version"], "action": "confirm_creation"}
    )
    assert response.status_code == 200, response.text
    novel = (await client.get(path)).json()
    response = await client.post(path + "/control", json={"version": novel["version"], "action": "new_scene"})
    assert response.status_code == 200, response.text
    novel = response.json()
    assert novel["chapters"][0]["entity"]["description"][0]["name"] == "章节目标"
    assert novel["scenes"][0]["entity"]["description"] == []
    scene_id = novel["scenes"][0]["entity"]["id"]
    renamed = await client.post(path + "/control", json={
        "version": novel["version"], "action": "rename_scene", "scene_id": scene_id, "name": "宫门盘查",
    })
    assert renamed.status_code == 200, renamed.text
    novel = renamed.json()
    assert novel["scenes"][0]["entity"]["name"] == "宫门盘查"
    locked = await client.post(
        path + "/template", json={"version": novel["version"], "template_id": template["id"]}
    )
    assert locked.status_code == 400
    removed = await client.delete(
        "/api/studio/templates/" + template["id"], params={"version": updated.json()["version"]}
    )
    assert removed.status_code == 200
    assert (await client.get(path)).json()["field_template_name"] == "人物驱动"


async def test_export_does_not_restore_template_fields_deleted_from_novel(client):
    from ainovel.core.studio_templates import TemplateFields

    fields = TemplateFields().model_dump(mode="json")
    fields["world"]["description"] = [{"name": "后来删除的字段", "value": [], "visibility": 2}]
    template = (
        await client.post("/api/studio/templates", json={"name": "初始化模板", "fields": fields})
    ).json()
    novel = (await client.post("/api/studio/novels", json={})).json()
    path = "/api/studio/novels/" + novel["id"]
    novel = (
        await client.post(
            path + "/template", json={"version": novel["version"], "template_id": template["id"]}
        )
    ).json()
    novel = (
        await client.post(
            path + "/edit",
            json={
                "version": novel["version"],
                "scope": "world",
                "target": novel["world"][0]["id"],
                "delete": True,
            },
        )
    ).json()
    assert novel["world"] == []
    exported = await client.post(
        path + "/template/export", json={"version": novel["version"], "name": "删除后的模板"}
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["fields"]["world"]["description"] == []


async def test_archived_route_preview_reads_its_own_character_state_without_writing(client):
    n = (await client.post("/api/studio/novels", json={})).json()
    path = "/api/studio/novels/" + n["id"]
    await client.post(path + "/message", json={"version": n["version"], "message": "讨论灯塔故事"})
    n = (await client.get(path)).json()
    role = {
        "name": "甲",
        "special_attributes": [{"key": "情绪", "value_type": "str", "current_value": "平静"}],
    }
    n = (
        await client.post(
            path + "/edit", json={"version": n["version"], "scope": "characters", "entity": role}
        )
    ).json()
    role_id = n["characters"][0]["id"]

    async def control(action, **args):
        current = (await client.get(path)).json()
        result = await client.post(
            path + "/control", json={"version": current["version"], "action": action, **args}
        )
        assert result.status_code == 200, result.text
        return result.json()

    await control("confirm_creation")
    n = await control("new_scene")
    first = n["scenes"][0]["entity"]["id"]
    await control("confirm_scene", scene_id=first)
    n = await control("new_scene")
    second = n["scenes"][-1]["entity"]["id"]
    role["special_attributes"][0]["current_value"] = "警惕"
    edited = await client.post(
        path + "/edit",
        json={"version": n["version"], "scope": "characters", "target": role_id, "entity": role},
    )
    assert edited.status_code == 200, edited.text
    n = await control("branch", scene_id=first)
    archived = n["branches"][0]["id"]
    preview = await client.get(path + "/preview", params={"branch_id": archived, "scene_id": second})
    assert preview.status_code == 200, preview.text
    assert preview.json()["characters"][role_id]["special_attributes"][0]["current_value"] == "警惕"
    active = await client.get(path + "/preview", params={"scene_id": first})
    assert active.json()["characters"][role_id]["special_attributes"][0]["current_value"] == "平静"
    assert (await client.get(path)).json()["version"] == n["version"]
