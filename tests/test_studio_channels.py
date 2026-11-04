import json
from types import SimpleNamespace

import pytest
from test_studio import fixture

from ainovel.core.config import Settings
from ainovel.core.studio import ConversationMessage, StudioAction, StudioStep
from ainovel.core.studio_state import emit, role_context
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
        assert json.loads(history[0].content)["channel"] == "user_discussion"
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


@pytest.mark.parametrize("content", [None, "", " \n", "这是给用户的正常回答"])
async def test_no_tools_ends_gm_immediately_without_forced_pause_or_correction(content):
    n, s, _, _ = fixture()

    async def actor(*args):
        return StudioDecoder("gm").decode(ProviderMessage(role="assistant", content=content))

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", s)
    assert n.calls == 1 and not n.error and not n.busy
    assert [m.content for m in s.messages] == ([content] if content and content.strip() else [])
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
