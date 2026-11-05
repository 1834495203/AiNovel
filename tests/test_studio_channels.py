import json
from types import SimpleNamespace

import pytest
from test_studio import fixture

from ainovel.core.config import Settings
from ainovel.core.performance_timeline import rewind
from ainovel.core.studio import ConversationMessage, StudioAction, StudioScene, StudioStep
from ainovel.core.studio_state import emit, role_context, role_states
from ainovel.core.studio_tools import StudioDecoder, tool_schemas
from ainovel.llm.errors import StructuredOutputError
from ainovel.llm.schemas import ProviderMessage
from ainovel.service.studio import StudioRunner


@pytest.mark.parametrize("mode", ["creation", "gm", "role", "writing", "summary"])
def test_removed_pause_is_not_exposed_or_accepted(mode):
    schemas = tool_schemas(mode)
    assert "pause" not in {tool["name"] for tool in schemas}
    with pytest.raises(StructuredOutputError):
        StudioDecoder(mode).decode(
            ProviderMessage(
                role="assistant",
                tool_calls=[
                    {"id": "removed", "function": {"name": "pause", "arguments": '{"reason":"stop"}'}}
                ],
            )
        )


def test_remaining_tool_sets_keep_role_permissions_separate():
    assert {t["name"] for t in tool_schemas("gm")} == {
        "say_to_user",
        "say_to_group",
        "schedule_characters",
        "edit_fields",
        "reset_performance",
        "rewind_performance",
        "enter_character",
        "remove_character",
        "create_character",
    }
    assert {t["name"] for t in tool_schemas("creation")} == {"say_to_user", "edit_fields", "create_character"}
    assert {t["name"] for t in tool_schemas("role")} == {"edit_self", "leave"}


async def test_gm_speech_tools_have_distinct_audiences_and_report_destination():
    n, s, a, _ = fixture()
    emit(s, "enter", "甲入场", a.id)
    runner = StudioRunner(n, None, Settings(_env_file=None))
    private = await runner.execute(
        "gm", s, StudioAction(name="say_to_user", arguments={"text": "幕后改成悲剧"})
    )
    group = await runner.execute(
        "gm", s, StudioAction(name="say_to_group", arguments={"text": "甲，门外响起了敲门声。"})
    )
    question = await runner.execute(
        "gm", s, StudioAction(name="say_to_user", arguments={"text": "你想继续吗？"})
    )
    assert private.data["channel"] == question.data["channel"] == "user_discussion"
    assert group.data["channel"] == "character_group" and group.data["audience"] == [a.id]
    assert [m.content for m in s.messages] == ["幕后改成悲剧", "你想继续吗？"]
    assert s.events[-1].text == "甲，门外响起了敲门声。"
    actor_context = json.dumps(role_context(n, s, a.id), ensure_ascii=False)
    assert (
        "敲门声" in actor_context
        and "幕后改成悲剧" not in actor_context
        and "你想继续吗" not in actor_context
    )
    schemas = {t["name"]: t for t in tool_schemas("gm")}
    assert schemas["say_to_user"]["description"] != schemas["say_to_group"]["description"]
    assert schemas["say_to_user"]["parameters"] != schemas["say_to_group"]["parameters"]


async def test_gm_delivers_tools_then_ends_without_pause_or_extra_calls():
    n, s, a, _ = fixture()
    emit(s, "enter", "甲入场", a.id)
    s.messages.append(ConversationMessage(role="user", content="让甲听见敲门声"))
    sequence = [
        StudioStep(
            text="我先让角色听见敲门声，再向你反馈。",
            actions=[
                StudioAction(name="say_to_group", arguments={"text": "甲，你听见门外响起敲门声。"}),
                StudioAction(name="say_to_user", arguments={"text": "场景已推进，你可以检查演绎。"}),
            ],
        ),
        StudioAction(name="reply", text="本轮完成"),
    ]

    async def actor(mode, data, history, request):
        assert data["current_scene"]["channel"] == "character_group"
        assert history[0].role == "user" and history[0].content == "让甲听见敲门声"
        if n.calls == 2:
            assert history[-1].role == "tool"
        return sequence.pop(0)

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", s)
    assert not n.error and n.calls == 2
    assert [m.content for m in s.messages] == [
        "让甲听见敲门声",
        "我先让角色听见敲门声，再向你反馈。",
        "场景已推进，你可以检查演绎。",
        "本轮完成",
    ]
    assert s.events[-1].text == "甲，你听见门外响起敲门声。"
    visible = json.dumps(role_context(n, s, a.id), ensure_ascii=False)
    assert "我先让角色" not in visible and "本轮完成" not in visible


async def test_private_direction_and_self_edits_are_ordered_for_writing_and_rewind():
    n, s, a, b = fixture()
    emit(s, "enter", "甲入场", a.id)
    emit(s, "enter", "乙入场", b.id)
    hint = "只有你听见了暗门里的声音"
    gm = iter(
        [
            StudioAction(name="schedule_characters", arguments={"characters": [a.id], "direction": hint}),
            StudioAction(name="schedule_characters", arguments={"characters": [b.id]}),
            StudioAction(name="say_to_group", arguments={"text": "灯塔里的灯亮了。"}),
            StudioAction(name="reply", text="演绎完成"),
        ]
    )

    async def actor(mode, data, history, request):
        if mode == "gm":
            return next(gm)
        if mode == "writing":
            assert [e["kind"] for e in data["events"]] == [
                "enter",
                "enter",
                "gm_direction",
                "role",
                "self_edit",
                "self_edit",
                "role",
                "role",
                "gm",
            ]
            direction = data["events"][2]
            assert direction["actor_id"] == a.id and direction["audience"] == [a.id]
            assert direction["text"] == hint
            assert '"平静" → "警觉"' in data["events"][4]["text"]
            assert '"警觉" → "紧张"' in data["events"][5]["text"]
            return StudioAction(name="reply", text="整幕正文")
        if data["self"]["id"] == b.id:
            context = json.dumps(data, ensure_ascii=False)
            assert hint not in context and "紧张" not in context
            assert "我去检查" in context
            return StudioAction(name="reply", text="我留在这里。")
        assert data["direction"] == hint
        assert hint not in json.dumps(data["history"], ensure_ascii=False)
        if history:
            assert data["self"]["special_attributes"][0]["current_value"] == "紧张"
            return StudioAction(name="reply", text="这里好像有动静。")
        return StudioStep(
            text="我去检查。",
            actions=[
                StudioAction(
                    name="edit_self",
                    arguments={
                        "operations": [
                            {"op": "replace", "path": "/special_attributes/0/current_value", "value": value}
                        ]
                    },
                )
                for value in ("警觉", "紧张")
            ],
        )

    async def save(novel):
        pass

    runner = StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor)
    with pytest.raises(ValueError, match="只发给一个角色"):
        await runner.execute(
            "gm",
            s,
            StudioAction(
                name="schedule_characters", arguments={"characters": [a.id, b.id], "direction": hint}
            ),
        )
    await runner.run("gm", s)
    assert not n.error
    assert hint in json.dumps(role_context(n, s, a.id), ensure_ascii=False)
    assert hint not in json.dumps(role_context(n, s, b.id), ensure_ascii=False)

    copy = n.model_copy(deep=True)
    copied_scene = copy.scenes[0]
    rewind(copy, copied_scene, target=s.events[2].id)
    assert copied_scene.events[-1].kind == "gm_direction"
    assert role_states(copy, copied_scene)[a.id].special_attributes[0].current_value == "平静"
    rewind(copy, copied_scene, steps=1)
    assert all(event.kind != "gm_direction" for event in copied_scene.events)

    s.confirmed = True
    await runner.run("writing", s)
    assert not n.error and s.text == "整幕正文"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, []),
        ("", []),
        (" \n", []),
        ("这是给用户的正常回答", ["这是给用户的正常回答"]),
        ('{"channel":"user_discussion","speaker":"gm","text":"讨论回复"}', ["讨论回复"]),
    ],
)
async def test_no_tools_ends_gm_immediately_without_forced_pause_or_correction(content, expected):
    n, s, _, _ = fixture()

    async def actor(*args):
        return StudioDecoder("gm").decode(ProviderMessage(role="assistant", content=content))

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", s)
    assert n.calls == 1 and not n.error and not n.busy
    assert [m.content for m in s.messages] == expected
    assert s.events == []


async def test_identical_answers_in_separate_user_turns_are_not_suppressed():
    n, s, _, _ = fixture()

    async def actor(*args):
        return StudioAction(name="reply", text="可以")

    async def save(n):
        pass

    for question in ("可以改名吗？", "也可以调整情景吗？"):
        s.messages.append(ConversationMessage(role="user", content=question))
        await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", s)
    assert [m.content for m in s.messages if m.role == "assistant"] == ["可以", "可以"]


def test_reverted_display_metadata_is_read_without_losing_conversation_content():
    message = ConversationMessage.model_validate(
        {"id": "legacy", "role": "assistant", "content": "原始答复", "phase": "progress"}
    )
    assert message.content == "原始答复" and message.id == "legacy"
    assert "phase" not in message.model_dump()


def test_legacy_gm_envelope_is_read_as_text_without_changing_other_json():
    _, scene, _, _ = fixture()
    wrapped = json.dumps({"channel": "user_discussion", "speaker": "gm", "text": "第一段\n\n第二段"})
    data = scene.model_dump()
    data["messages"] = [
        {"id": "question", "role": "user", "content": wrapped},
        {"id": "reply", "role": "assistant", "content": wrapped},
        {"id": "example", "role": "assistant", "content": '{"text": "JSON 示例"}'},
    ]
    reloaded = StudioScene.model_validate(data)
    assert [(message.id, message.content) for message in reloaded.messages] == [
        ("question", wrapped),
        ("reply", "第一段\n\n第二段"),
        ("example", '{"text": "JSON 示例"}'),
    ]
