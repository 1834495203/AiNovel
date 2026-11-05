import json

import pytest
from test_studio import client as studio_client  # noqa: F401
from test_studio import fixture

from ainovel.core.prose_edits import ProseEdit, apply_prose_edits, prose_revision, read_prose
from ainovel.core.studio import StudioAction, WritingMessage
from ainovel.core.studio_tools import StudioDecoder, tools_for
from ainovel.core.studio_turns import checkpoint, restore_turn
from ainovel.llm.schemas import ProviderMessage
from ainovel.service.studio import StudioRunner


@pytest.fixture
async def client(studio_client):  # noqa: F811
    yield studio_client


@pytest.mark.parametrize(("count", "page_size"), [(100, 12), ("all", 12), (None, 3), (0, 1)])
def test_read_prose_page_size_is_bounded_after_tool_decoding(count, page_size):
    decoded = StudioDecoder("writing_chat").decode(
        ProviderMessage(
            role="assistant",
            tool_calls=[
                {
                    "id": "read",
                    "function": {"name": "read_prose", "arguments": json.dumps({"count": count})},
                }
            ],
        )
    )
    text = "\n\n".join(f"第 {i} 段" for i in range(1, 15))
    args = decoded.actions[0].arguments
    first = read_prose(text, **args)
    assert len(first["paragraphs"]) == page_size
    assert first["next_start"] == page_size + 1
    second = read_prose(text, **{**args, "start": first["next_start"]})
    assert second["paragraphs"][0]["paragraph"] == page_size + 1


def test_prose_edits_apply_against_original_positions_and_preserve_separators():
    original = "  她说：好。\r\n\r\n\r\n他也说：好。\r\n"
    revision = prose_revision(original)
    located = read_prose(original, 1, 2, "")
    assert located["paragraphs"][0]["separator_after"] == "\r\n\r\n\r\n"
    assert [p["paragraph"] for p in read_prose(original, 1, 2, "他")["paragraphs"]] == [2]
    assert read_prose("第一段\n第二段", 1, 2, "")["paragraph_count"] == 2
    new, changes = apply_prose_edits(
        original,
        revision,
        [
            ProseEdit(search="好。", replacement="可以。", paragraph=2),
            ProseEdit(search="她说", replacement="她低声说"),
            ProseEdit(search="他也说", replacement="这时，", operation="insert_before"),
        ],
    )
    assert new == "  她低声说：好。\r\n\r\n\r\n这时，他也说：可以。\r\n"
    assert [(change.before, change.after) for change in changes] == [
        ("好。", "可以。"),
        ("她说", "她低声说"),
        ("", "这时，"),
    ]
    assert (
        apply_prose_edits(original, revision, [ProseEdit(search="好。", replacement="", replace_all=True)])[
            1
        ][0].count
        == 2
    )


@pytest.mark.parametrize(
    ("revision", "edits", "error"),
    [
        ("stale", [("北门", "侧门")], "版本已变化"),
        (None, [("北门", "侧门")], "匹配多处"),
        (None, [("东门", "侧门"), ("未出现", "其他")], "未匹配"),
        (None, [("从北门到东门", "绕路"), ("东门", "侧门")], "范围重叠"),
    ],
)
def test_invalid_prose_batch_rejects_every_edit(revision, edits, error):
    text = "她从北门到东门，又回到北门。"
    with pytest.raises(ValueError, match=error):
        apply_prose_edits(
            text,
            revision or prose_revision(text),
            [ProseEdit(search=old, replacement=new) for old, new in edits],
        )


async def test_writing_chat_edits_prose_and_creation_without_changing_gm_discussion(client, monkeypatch):
    n = (await client.post("/api/studio/novels", json={})).json()
    path = f"/api/studio/novels/{n['id']}"

    async def post(suffix, **data):
        nonlocal n
        response = await client.post(path + suffix, json={"version": n["version"], **data})
        assert response.status_code == 200, response.text
        n = (await client.get(path)).json()

    await post("/message", message="写灯塔故事")
    await post("/control", action="confirm_creation")
    await post("/control", action="new_scene")
    scene_id = n["scenes"][0]["entity"]["id"]
    await post("/message", scene_id=scene_id, message="灯塔开场")
    original_gm_messages = n["scenes"][0]["messages"]
    await post("/control", action="confirm_scene", scene_id=scene_id)
    original_world = n["world"]

    original = "她看向北门。\n\n北门还锁着。"
    calls = iter(
        [
            ("read_scene", {}),
            ("write_prose", {"revision": prose_revision(""), "text": original}),
            ("reply", {}),
            ("read_prose", {"query": "北门"}),
            (
                "edit_prose",
                {
                    "revision": prose_revision(original),
                    "edits": [{"search": "北门", "replacement": "侧门", "replace_all": True}],
                },
            ),
            (
                "edit_fields",
                {
                    "operations": [
                        {"op": "add", "path": "/-", "value": {"name": "节奏", "type": "creation"}}
                    ],
                },
            ),
            ("reply", {}),
        ]
    )
    fallback = StudioRunner.mock

    def mock(self, mode, data, history):
        if mode != "writing_chat":
            return fallback(self, mode, data, history)
        assert "text" not in data and "events" not in data
        name, arguments = next(calls)
        if name == "write_prose":
            assert json.loads(history[-1].content)["data"]["events"]
        if name == "edit_prose":
            assert json.loads(history[-1].content)["data"]["matches"] == 2
        return StudioAction(name=name, arguments=arguments, text="完成" if name == "reply" else "")

    monkeypatch.setattr(StudioRunner, "mock", mock)
    await post("/writing-message", scene_id=scene_id, message="写出整幕")
    assert n["scenes"][0]["text"] == original and not n["error"]
    # A newer, editable scene can coexist with editing the confirmed prose.
    await post("/control", action="new_scene")
    later_id = n["scenes"][1]["entity"]["id"]
    await post("/message", scene_id=later_id, message="另一幕的演绎")
    previous_timeline = n["scenes"][1]["timeline"]
    await post("/writing-message", scene_id=scene_id, message="北门全换成侧门，记下节奏")
    scene = n["scenes"][0]
    assert not n["error"] and scene["text"] == "她看向侧门。\n\n侧门还锁着。"
    assert scene["messages"] == original_gm_messages and n["world"] == original_world
    receipt = next(m for m in scene["writing_messages"] if m["changes"])
    assert receipt["changes"] == [{"paragraph": None, "before": "北门", "after": "侧门", "count": 2}]
    assert n["creation"][0]["name"] == "节奏"
    assert n["scenes"][1]["timeline"]["operations"][-1]["origin"] == "writer"
    assert n["chapters"][0]["summary_stale"]
    assert not n["scenes"][1]["writing_messages"]

    async def turn(action, **data):
        checkpoint = n["turn_checkpoint"]
        assert checkpoint["channel"] == "writing" and checkpoint["version"] == n["version"]
        await post(
            "/turn",
            channel="writing",
            scene_id=scene_id,
            message_id=checkpoint["message_id"],
            action=action,
            **data,
        )

    for action, replacement, new_message in [
        ("retry", "侧门", {}),
        ("edit", "东门", {"message": "北门全换成东门，记下节奏"}),
    ]:
        calls = iter(
            [
                ("read_prose", {"query": "北门"}),
                (
                    "edit_prose",
                    {
                        "revision": prose_revision(original),
                        "edits": [{"search": "北门", "replacement": replacement, "replace_all": True}],
                    },
                ),
                (
                    "edit_fields",
                    {
                        "operations": [
                            {"op": "add", "path": "/-", "value": {"name": "节奏", "type": "creation"}}
                        ],
                    },
                ),
                ("reply", {}),
            ]
        )
        await turn(action, **new_message)
        assert not n["error"] and n["scenes"][0]["text"] == original.replace("北门", replacement)
        assert [field["name"] for field in n["creation"]] == ["节奏"]
        assert len([m for m in n["scenes"][0]["writing_messages"] if m["role"] == "user"]) == 2
    await turn("withdraw")
    assert n["scenes"][0]["text"] == original
    assert n["scenes"][0]["confirmed"]
    assert n["scenes"][0]["messages"] == original_gm_messages
    assert n["creation"] == [] and n["scenes"][1]["timeline"] == previous_timeline

    with pytest.raises(ValueError):
        tools_for("writing_chat")["edit_fields"].model_validate(
            {"scope": "world", "operations": [{"op": "add", "path": "/-", "value": {}}]}
        )


def test_legacy_writing_turn_removes_chat_only_and_new_turn_requires_its_checkpoint():
    novel, scene, _, _ = fixture()
    scene.confirmed = True
    scene.text = "现有正文"
    old = WritingMessage(role="user", content="旧要求")
    scene.writing_messages.extend([old, WritingMessage(role="assistant", content="已完成")])
    checkpoint(novel, scene, "gm-message")
    assert restore_turn(novel, scene.entity.id, old.id, channel="writing", preserve_state=True) == "旧要求"
    assert not scene.writing_messages and scene.text == "现有正文"
    assert novel.turn_checkpoint.message_id == "gm-message"
    new = WritingMessage(role="user", content="新要求", checkpointed=True)
    scene.writing_messages.append(new)
    with pytest.raises(ValueError, match="回退记录已被"):
        restore_turn(novel, scene.entity.id, new.id, channel="writing", preserve_state=True)
