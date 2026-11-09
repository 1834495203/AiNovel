import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_studio import client as studio_client  # noqa: F401
from test_studio import fixture

from ainovel.core.config import Settings
from ainovel.core.studio import ConversationMessage, Patch, StudioAction, StudioNovel
from ainovel.core.studio_tools import StudioDecoder
from ainovel.core.studio_turns import checkpoint, restore_turn
from ainovel.llm.errors import StructuredOutputError
from ainovel.llm.schemas import ProviderMessage
from ainovel.service.studio import StudioRunner


@pytest.fixture
async def client(studio_client):  # noqa: F811
    yield studio_client


def batch(mode, *calls):
    return StudioDecoder(mode).decode(
        ProviderMessage(
            role="assistant",
            tool_calls=[
                {"id": f"tool_{i}", "function": {"name": name, "arguments": json.dumps(args)}}
                for i, (name, args) in enumerate(calls)
            ],
        )
    )


async def test_batch_runs_in_order_and_returns_all_tool_ids_in_one_assistant_message():
    n = StudioNovel()
    step = batch("creation", ("create_character", {"name": "甲"}), ("create_character", {"name": "乙"}))
    seen = []

    async def actor(mode, data, history, request):
        seen.append(history.copy())
        if len(seen) == 1:
            return step
        assert [c.id for c in history[0].tool_calls] == ["tool_0", "tool_1"]
        assert [m.tool_call_id for m in history[1:]] == ["tool_0", "tool_1"]
        assert [c["name"] for c in data["characters"]] == ["甲", "乙"]
        return StudioAction(name="reply", text="创建完成")

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("creation")
    assert not n.error and n.calls == 2


async def test_reset_skips_later_actions_and_restarts_with_clean_context():
    n, scene, _, _ = fixture()

    async def actor(mode, data, history, request):
        if n.calls == 1:
            return batch("gm", ("reset_performance", {}), ("create_character", {"name": "不应创建"}))
        assert history == []
        return StudioAction(name="reply", text="重置完成")

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).run("gm", scene)
    assert "不应创建" not in [c.name for c in n.characters]
    assert not n.error and n.calls == 2


async def test_role_leaving_skips_later_state_edits():
    from ainovel.core.studio_state import emit, role_states

    n, s, a, _ = fixture()
    emit(s, "enter", "到场", a.id)

    async def actor(mode, data, history, request):
        return batch(
            "role",
            ("leave", {}),
            (
                "edit_fields",
                {
                    "operations": [
                        {"op": "replace", "path": "/special_attributes/0/current_value", "value": "不应修改"}
                    ]
                },
            ),
        )

    async def save(n):
        pass

    await StudioRunner(n, SimpleNamespace(save=save), Settings(_env_file=None), actor=actor).role(
        s, a, "行动"
    )
    assert role_states(n, s)[a.id].special_attributes[0].current_value == "平静"


def test_whole_batch_validated_before_any_tool_runs():
    with pytest.raises(ValidationError):
        batch("creation", ("create_character", {"name": "甲"}), ("create_character", {}))
    with pytest.raises(StructuredOutputError):
        batch("creation", ("say_to_group", {"text": "无权限"}))


async def send(client, n, message, scene_id=""):
    path = f"/api/studio/novels/{n['id']}"
    r = await client.post(
        path + "/message", json={"version": n["version"], "message": message, "scene_id": scene_id}
    )
    assert r.status_code == 200, r.text
    return (await client.get(path)).json()


async def change(client, n, action, message="", scene_id=""):
    return await client.post(
        f"/api/studio/novels/{n['id']}/turn",
        json={
            "version": n["version"],
            "message_id": n["turn_checkpoint"]["message_id"],
            "scene_id": scene_id,
            "action": action,
            "message": message,
        },
    )


async def test_creation_retry_edit_withdraw_restore_fields_and_remove_ai_replies(client):
    n = (await client.post("/api/studio/novels", json={})).json()
    original = n
    n = await send(client, n, "灯塔故事")
    assert "before" not in n["turn_checkpoint"]
    first_calls = n["calls"]
    assert len(n["world"]) == 1
    r = await change(client, n, "retry")
    assert r.status_code == 200, r.text
    n = (await client.get(f"/api/studio/novels/{n['id']}")).json()
    assert len(n["world"]) == 1 and n["calls"] > first_calls
    assert len([m for m in n["creation_messages"] if m["role"] == "user"]) == 1
    r = await change(client, n, "edit", "改成山村故事")
    assert r.status_code == 200, r.text
    n = (await client.get(f"/api/studio/novels/{n['id']}")).json()
    assert "灯塔故事" not in json.dumps(n, ensure_ascii=False)
    assert "山村故事" in n["world"][0]["description"][0]["value"][0]
    r = await change(client, n, "withdraw")
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["world"] == original["world"] and after["creation_messages"] == []
    assert after["turn_checkpoint"] is None and after["calls"] == n["calls"]


async def test_manual_change_blocks_rollback_and_stale_version_is_rejected(client):
    n = (await client.post("/api/studio/novels", json={})).json()
    n = await send(client, n, "灯塔故事")
    r = await client.post(
        f"/api/studio/novels/{n['id']}/edit",
        json={"version": n["version"], "scope": "creation", "title": "手动改名"},
    )
    assert r.status_code == 200
    assert (await change(client, n, "withdraw")).status_code == 409
    n = r.json()
    rejected = await change(client, n, "withdraw")
    assert rejected.status_code == 400 and "资料已发生修改" in rejected.text


async def test_failed_turn_restores_partial_tool_effects(client, monkeypatch):
    async def actor(self, mode, data, history):
        if not history or history[-1].role == "user":
            return batch(
                mode, ("create_character", {"name": "临时人物"}), ("say_to_user", {"text": "已创建"})
            )
        raise RuntimeError("测试模型断线")

    monkeypatch.setattr(StudioRunner, "call", actor)
    n = (await client.post("/api/studio/novels", json={})).json()
    n = await send(client, n, "创建人物")
    assert n["error"] and len(n["characters"]) == 1
    r = await change(client, n, "withdraw")
    assert r.status_code == 200, r.text
    assert r.json()["characters"] == [] and r.json()["creation_messages"] == []


def test_scene_checkpoint_restores_presence_events_role_patches_and_rejects_confirmed_scene():
    from ainovel.core.studio_state import edit_self, emit

    n, s, a, _ = fixture()
    msg = ConversationMessage(role="user", content="让甲离场")
    checkpoint(n, s, msg.id)
    s.messages.append(msg)
    emit(s, "enter", "到场", a.id)
    edit_self(n, s, a.id, [Patch(op="replace", path="/special_attributes/0/current_value", value="恐惧")])
    n.turn_checkpoint.version = n.version
    restored = StudioNovel.model_validate_json(n.model_dump_json())
    restored.scenes[-1].confirmed = True
    with pytest.raises(ValueError):
        restore_turn(restored, s.entity.id, msg.id)
    restore_turn(n, s.entity.id, msg.id)
    assert n.scenes[-1].events == [] and n.scenes[-1].character_patches == {}
    assert n.scenes[-1].messages == []


@pytest.mark.parametrize("action", ["withdraw", "edit", "retry"])
async def test_legacy_message_actions_require_acknowledgement_and_preserve_fields(
    client, monkeypatch, action
):
    n = (await client.post("/api/studio/novels", json={})).json()
    with monkeypatch.context() as old_server:
        old_server.setattr("ainovel.controller.studio.checkpoint", lambda *args: None)
        n = await send(client, n, "原始旧消息")
    assert n["turn_checkpoint"] is None
    path = f"/api/studio/novels/{n['id']}"
    payload = {
        "version": n["version"],
        "action": action,
        "message_id": n["creation_messages"][0]["id"],
        "message": "修改的新消息",
    }
    rejected = await client.post(path + "/turn", json=payload)
    assert rejected.status_code == 400
    result = await client.post(path + "/turn", json={**payload, "preserve_state": True})
    assert result.status_code == 200, result.text
    after = (await client.get(path)).json()
    assert after["world"][0] == n["world"][0]
    if action == "withdraw":
        assert after["creation_messages"] == [] and after["calls"] == n["calls"]
    else:
        users = [m for m in after["creation_messages"] if m["role"] == "user"]
        assert len(users) == 1
        assert users[0]["content"] == ("修改的新消息" if action == "edit" else "原始旧消息")
        assert after["turn_checkpoint"] is not None


def test_preserve_state_flag_does_not_bypass_new_turn_guard_or_frozen_scenes():
    n, s, _, _ = fixture()
    msg = ConversationMessage(role="user", content="新消息")
    checkpoint(n, s, msg.id)
    s.messages.append(msg)
    n.version += 1
    with pytest.raises(ValueError, match="资料已发生修改"):
        restore_turn(n, s.entity.id, msg.id, preserve_state=True)
    n.turn_checkpoint = None
    s.confirmed = True
    with pytest.raises(ValueError):
        restore_turn(n, s.entity.id, msg.id, preserve_state=True)
