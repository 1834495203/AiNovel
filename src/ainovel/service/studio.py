"""Ordinary GM/role tool loops; no graph or automatic writing pipeline."""

import json
import math
import re
from pathlib import Path
from uuid import UUID

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from ainovel.core.entities import Entity
from ainovel.core.performance_timeline import record_change, rewind, timeline_context
from ainovel.core.studio import ConversationMessage, StudioAction, StudioStep, ToolOutcome, gm_discussion_text
from ainovel.core.studio_state import (
    apply_patch,
    edit_self,
    emit,
    present,
    require_editable,
    reset,
    role_context,
    role_states,
)
from ainovel.core.studio_templates import initialize_entity
from ainovel.core.studio_tools import TOOLS, StudioDecoder, TextDecoder, tool_schemas, tools_for
from ainovel.llm.contracts import CallMetadata, CompletionRequest
from ainovel.llm.gateway import ModelGateway
from ainovel.llm.schemas import (
    ChatBody,
    FunctionCall,
    FunctionDefinition,
    Message,
    ToolCall,
    ToolDefinition,
    ToolParameters,
)
from ainovel.llm.transport import OpenAICompatibleTransport

MODES = ("creation", "gm", "role", "writing", "summary")
ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
ENV.globals.clear()


def defaults():
    root = Path(__file__).resolve().parents[1] / "prompts"
    return {mode: (root / f"studio_{mode}.j2").read_text(encoding="utf-8") for mode in MODES}


def resolve_character(novel, name):
    found = [c for c in novel.characters if c.id == name or c.name == name]
    if len(found) != 1:
        raise ValueError("角色不存在或重名，请使用返回的已有 ID")
    return found[0]


def scene_data(scene, *, positions=False):
    return {
        "scene": scene.entity.model_dump(mode="json"),
        "events": [
            {
                **({"id": e.id, "position": index + 1} if positions else {}),
                "kind": e.kind,
                "actor_id": e.actor_id,
                "text": e.text,
                **({"audience": e.audience} if e.kind in ("gm_direction", "self_edit") else {}),
            }
            for index, e in enumerate(scene.events)
        ],
        "text": scene.text,
    }


def gm_context(novel, scene):
    chapters = []
    target_index = next(i for i, c in enumerate(novel.chapters) if c.entity.id == scene.chapter_id)
    for index, chapter in enumerate(novel.chapters[: target_index + 1]):
        distance = target_index - index
        scenes = [s for s in novel.scenes if s.chapter_id == chapter.entity.id]
        # No future scenes even when chapters have already been planned.
        scenes = [s for s in scenes if novel.scenes.index(s) <= novel.scenes.index(scene)]
        scenes = [s for s in scenes if s.entity.id != scene.entity.id]
        if distance < novel.compression.full_chapters or chapter.summary_stale or not chapter.summary:
            content = [scene_data(s) for s in scenes]
            level = "full"
        elif distance < novel.compression.full_chapters + novel.compression.scene_chapters:
            content = [
                {
                    "scene": s.entity.model_dump(mode="json"),
                    "events": scene_data(s)["events"],
                }
                for s in scenes
            ]
            level = "scenes"
        else:
            content = chapter.summary
            level = "summary"
        chapters.append({"name": chapter.entity.name, "level": level, "content": content})
    return {
        "title": novel.title,
        "world": [e.model_dump(mode="json") for e in novel.world],
        "creation": [e.model_dump(mode="json") for e in novel.creation],
        "characters": [e.model_dump(mode="json") for e in role_states(novel, scene).values()],
        "chapter_scenes": [
            s.entity.model_dump(mode="json") for s in novel.scenes if s.chapter_id == scene.chapter_id
        ],
        "current_scene": {
            **scene_data(scene, positions=True),
            "channel": "character_group",
            "audience": present(scene),
        },
        "present": present(scene),
        "present_count": len(present(scene)),
        "timeline": timeline_context(scene),
        "chapters": chapters,
    }


def conversation_history(messages):
    return [Message(role=m.role, content=m.content) for m in messages]


def build_request(settings, novel, mode, data, history=None):
    source = novel.prompts.get(mode, defaults()[mode])
    try:
        role_prompt = ENV.from_string(source).render(data=data)
        context = ENV.from_string(novel.context_source).render(data=data)
    except TemplateError as error:
        raise ValueError(f"Jinja 模板无法渲染：{error}") from error
    schemas = tool_schemas(mode) if mode not in ("writing", "summary") else []
    # Return schemas remain separate from editable behavior/context templates.
    body = ChatBody(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        messages=[
            Message(role="system", content=role_prompt),
            Message(
                role="system",
                content=json.dumps(
                    {"tool_result_schema": ToolOutcome.model_json_schema()}, ensure_ascii=False
                ),
            ),
            Message(role="user", content=context),
            *(history or []),
        ],
        tools=[
            ToolDefinition(
                function=FunctionDefinition(
                    name=s["name"], description=s["description"], parameters=ToolParameters(s["parameters"])
                )
            )
            for s in schemas
        ]
        or None,
        tool_choice="auto" if schemas else None,
    )
    return CompletionRequest(
        body=body,
        metadata=CallMetadata(
            node=mode, summary=f"{mode} 普通工具循环", prompt_version="studio-v1", error_label=mode
        ),
    )


def estimate(request):
    text = request.body.model_dump_json(exclude_none=True)
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return {
        "input_tokens": math.ceil(cjk * 1.5 + (len(text) - cjk) / 3.5),
        "method": "中英文字符混合估算，实际以供应商计费为准",
        "characters": len(text),
    }


def edit_fields(novel, scene, args):
    scope, target = args.scope, args.target
    if scope == "characters":
        if not target:
            raise ValueError("新建角色使用 create_character；修改角色请指定目标")
        entity = resolve_character(novel, target)
        if scene and scene.character_patches.get(entity.id):
            raise ValueError("该角色已有演绎变化，请先重置演绎再修改其设定")
        base = role_states(novel, scene)[entity.id] if scene else entity
        candidate = Entity.model_validate(apply_patch(base.model_dump(mode="json"), args.operations))
        if candidate.id != entity.id:
            raise ValueError("不能修改内部 ID")
        if any(c.id != entity.id and c.name == candidate.name for c in novel.characters):
            raise ValueError("角色名字重复")
        if scene:
            scene.settings_patches.setdefault(entity.id, []).extend(args.operations)
        else:
            novel.characters[novel.characters.index(entity)] = candidate
        return candidate.model_dump(mode="json")
    collection = getattr(novel, scope)
    if target:
        matches = [
            e
            for e in collection
            if (e.entity if scope in ("chapters", "scenes") else e).id == target
            or (e.entity if scope in ("chapters", "scenes") else e).name == target
        ]
        if len(matches) != 1:
            raise ValueError("目标不存在或重名")
        item = matches[0]
        if scope == "scenes" and (not scene or item.entity.id != scene.entity.id):
            raise ValueError("只能修改当前幕，历史幕需要分支")
        if scope == "chapters" and scene and item.entity.id != scene.chapter_id:
            raise ValueError("只能修改当前章节资料")
        entity = item.entity if scope in ("chapters", "scenes") else item
        candidate = Entity.model_validate(apply_patch(entity.model_dump(mode="json"), args.operations))
        if candidate.id != entity.id:
            raise ValueError("不能修改内部 ID")
        if scope in ("chapters", "scenes"):
            item.entity = candidate
        else:
            collection[collection.index(item)] = candidate
        return candidate.model_dump(mode="json")
    if scope not in ("world", "creation"):
        raise ValueError("此类别需要指定目标")
    values = apply_patch([e.model_dump(mode="json") for e in collection], args.operations)
    if not isinstance(values, list):
        raise ValueError("资料类别必须是实体列表")
    known = {e.id for e in collection}
    entities = []
    for value in values:
        if isinstance(value, dict) and value.get("id") not in known:
            value = {key: item for key, item in value.items() if key != "id"}
        entities.append(Entity.model_validate(value))
    if len({e.id for e in entities}) != len(entities):
        raise ValueError("实体 ID 重复")
    setattr(novel, scope, entities)
    return [e.model_dump(mode="json") for e in entities]


class BudgetReached(Exception):
    pass


class StudioRunner:
    def __init__(self, novel, repository, settings, *, actor=None, origin="gm"):
        self.novel, self.repository, self.settings = novel, repository, settings
        self.actor = actor
        self.used = 0
        self.gateway = None
        self.origin = origin

    def change(self, scene, kind, summary, mutate, *, origin=None, actor_id=""):
        result, operation = record_change(
            self.novel, scene, kind, summary, mutate, origin=origin or self.origin, actor_id=actor_id
        )
        if isinstance(result, ToolOutcome) and operation:
            result.data = {
                **(result.data if isinstance(result.data, dict) else {"result": result.data}),
                "operation": {
                    "id": operation.id,
                    "parent": operation.parent,
                    "event_ids": operation.event_ids,
                },
            }
        return result

    def send_user_message(self, scene, text):
        """Store each actual user-facing message without content-based suppression."""
        if scene is not None:
            text = gm_discussion_text(text)
        if not text.strip():
            return False
        message = ConversationMessage(role="assistant", content=text)
        (scene.messages if scene else self.novel.creation_messages).append(message)
        return True

    async def save_progress(self):
        if self.novel.turn_checkpoint and self.novel.running.split(" · ")[0] in ("creation", "gm", "role"):
            self.novel.turn_checkpoint.version = self.novel.version + 1
        await self.repository.save(self.novel)

    async def record(self, command_id, trace):
        self.novel.input_tokens += trace.usage.input_tokens or 0
        self.novel.output_tokens += trace.usage.output_tokens or 0

    async def call(self, mode, data, history):
        if self.used >= self.novel.compression.max_calls:
            raise BudgetReached()
        self.used += 1
        self.novel.calls += 1
        self.novel.running = mode + (" · " + data["self"]["name"] if mode == "role" else "")
        await self.save_progress()
        request = build_request(self.settings, self.novel, mode, data, history)
        if self.actor:
            return await self.actor(mode, data, history, request)
        if self.settings.llm_provider == "mock":
            return self.mock(mode, data, history)
        if self.gateway is None:
            config = self.settings.model_copy(update={"llm_output_retries": 0})
            self.gateway = ModelGateway(config, self, OpenAICompatibleTransport(config))
        decoder = TextDecoder() if mode in ("writing", "summary") else StudioDecoder(mode)
        result = await self.gateway.complete(request, decoder, UUID(self.novel.run_id))
        return StudioAction(name="reply", text=result.text) if mode in ("writing", "summary") else result

    def mock(self, mode, data, history):
        calls = [t.tool_calls[0].function.name for t in history if t.tool_calls]
        if mode == "creation" and "edit_fields" not in calls:
            user = next((m.content for m in reversed(history) if m.role == "user"), "故事构思")
            return StudioAction(
                name="edit_fields",
                arguments={
                    "scope": "world",
                    "target": "",
                    "operations": [
                        {
                            "op": "add",
                            "path": "/-",
                            "value": {
                                "name": "故事方向",
                                "type": "world",
                                "description": [{"name": "构思", "value": [user]}],
                            },
                        }
                    ],
                },
            )
        if mode == "gm" and "say_to_group" not in calls:
            return StudioAction(
                name="say_to_group", arguments={"text": "场景开始。各位根据自己的目标决定如何行动。"}
            )
        if mode == "gm" and data["present"] and "schedule_characters" not in calls:
            return StudioAction(
                name="schedule_characters",
                arguments={"characters": data["present"]},
            )
        if mode == "role":
            return StudioAction(name="reply", text=data["self"]["name"] + "：我先观察周围，再决定是否靠近。")
        if mode in ("writing", "summary"):
            events = data.get("events", []) or [
                event for s in data.get("scenes", []) for event in s["events"]
            ]
            content = "\n\n".join(e["text"] for e in events if e["kind"] in ("gm", "role"))
            return StudioAction(name="reply", text=content or "这一章记录了已经确认的事件。")
        return StudioAction(
            name="reply",
            text="已根据你的构思整理资料，可以继续补充或确认创建。"
            if mode == "creation"
            else "本轮演绎已完成，你可以检查结果或提出调整。",
        )

    async def role(self, scene, character, direction):
        require_editable(self.novel, scene)
        direction = direction.strip()
        direction_id = ""
        if direction:
            event = self.change(
                scene,
                "gm_direction",
                "GM 私下提示：" + character.name,
                lambda n, s: emit(s, "gm_direction", direction, character.id, audience=[character.id]),
                actor_id=character.id,
            )
            direction_id = event.id
        history = []
        while True:
            data = role_context(self.novel, scene, character.id, current_direction_id=direction_id)
            data["direction"] = direction
            action = await self.call("role", data, history)
            if isinstance(action, StudioAction) and action.name == "reply":
                self.change(
                    scene,
                    "role_reply",
                    character.name + "发言",
                    lambda n, s: emit(s, "role", action.text, character.id),
                    origin="role",
                    actor_id=character.id,
                )
                await self.save_progress()
                return action.text
            actions, text = self.begin_step(history, action)
            if text:
                self.change(
                    scene,
                    "role_reply",
                    character.name + "发言",
                    lambda n, s: emit(s, "role", text, character.id),
                    origin="role",
                    actor_id=character.id,
                )
            departed = False
            for item in actions:
                if departed:
                    outcome = ToolOutcome(success=False, message="角色已离场，未执行")
                else:
                    if item.name not in tools_for("role"):
                        raise ValueError("角色没有此工具权限")
                    args = TOOLS[item.name].model_validate(item.arguments)
                    try:
                        if item.name == "leave":
                            self.change(
                                scene,
                                "leave",
                                character.name + "离场",
                                lambda n, s: emit(
                                    s, "leave", args.reason or character.name + "离场", character.id
                                ),
                                origin="role",
                                actor_id=character.id,
                            )
                            departed = True
                            outcome = ToolOutcome(message="已离场")
                        else:
                            edited = self.change(
                                scene,
                                "edit_self",
                                character.name + "更新自身字段",
                                lambda n, s: edit_self(n, s, character.id, args.operations),
                                origin="role",
                                actor_id=character.id,
                            )
                            outcome = ToolOutcome(
                                message="自身状态已修改，仅本次演绎生效" if edited else "字段值未发生变化"
                            )
                    except ValueError as error:
                        outcome = ToolOutcome(success=False, message=str(error))
                self.tool_result(history, item, outcome)
                await self.save_progress()
            if departed:
                return character.name + "已离场"

    @staticmethod
    def begin_step(history, step):
        actions = step.actions if isinstance(step, StudioStep) else [step]
        for index, item in enumerate(actions):
            if not item.call_id:
                item.call_id = f"call_{len(history)}_{index}"
        text = step.text
        history.append(
            Message(
                role="assistant",
                content=text,
                tool_calls=[
                    ToolCall(
                        id=item.call_id,
                        function=FunctionCall(
                            name=item.name, arguments=json.dumps(item.arguments, ensure_ascii=False)
                        ),
                    )
                    for item in actions
                ],
            )
        )
        return actions, text

    @staticmethod
    def tool_result(history, action, result):
        history.append(Message(role="tool", tool_call_id=action.call_id, content=result.model_dump_json()))

    async def execute(self, mode, scene, action):
        if mode == "gm":
            require_editable(self.novel, scene)
        if action.name not in tools_for(mode):
            raise ValueError("当前模式没有该工具权限")
        args = tools_for(mode)[action.name].model_validate(action.arguments)
        novel = self.novel
        if action.name == "rewind_performance":
            data = rewind(self.novel, scene, steps=args.steps, target=args.target)
            return ToolOutcome(
                message="已从起始状态重放到选定位置；后续操作不再生效，用户讨论保留", data=data
            )
        if action.name == "schedule_characters":
            if args.direction.strip() and len(args.characters) != 1:
                raise ValueError("私下提示只发给一个角色；共有情景和引导请用 say_to_group 发到群聊")
            replies = []
            for name in args.characters:
                character = resolve_character(novel, name)
                if character.id not in present(scene):
                    replies.append({"character": character.name, "skipped": "不在场，未运行"})
                    continue
                reply = await self.role(scene, character, args.direction)
                replies.append({"character": character.name, "reply": reply})
            return ToolOutcome(
                message="角色回复已写入角色群聊。请用 say_to_group 描述客观结果，不要把角色回复转发给用户代替场景叙述。",
                data={"channel": "character_group", "replies": replies},
            )
        if mode == "gm" and action.name != "say_to_user":
            summaries = {
                "say_to_group": "GM 群聊发言",
                "reset_performance": "重置演绎",
                "create_character": "创建角色：" + getattr(args, "name", ""),
                "edit_fields": "修改资料：" + getattr(args, "scope", "") + " " + getattr(args, "target", ""),
                "enter_character": "角色入场：" + getattr(args, "character", ""),
                "remove_character": "角色离场：" + getattr(args, "character", ""),
            }
            return self.change(
                scene,
                action.name,
                summaries[action.name],
                lambda n, s: self.execute_atomic(mode, n, s, action, args),
            )
        return self.execute_atomic(mode, self.novel, scene, action, args)

    def execute_atomic(self, mode, novel, scene, action, args):
        if action.name == "say_to_user":
            text = args.text
            sent = self.send_user_message(scene, text)
            if sent:
                delivery_message = "已发给用户，角色不可见" if mode == "gm" else "消息已发送"
            else:
                delivery_message = "没有发送空白消息"
            return ToolOutcome(
                message=delivery_message,
                data={"channel": "user_discussion", "sent": sent},
            )
        elif action.name == "say_to_group":
            event = emit(scene, "gm", args.text)
            return ToolOutcome(
                message="已发送到角色群聊，未写入用户讨论",
                data={
                    "channel": "character_group",
                    "audience": event.audience,
                    "event_id": event.id,
                    "sent": True,
                },
            )
        elif action.name == "reset_performance":
            reset(scene)
        elif action.name == "create_character":
            if any(e.name == args.name for e in novel.characters):
                raise ValueError("角色名字已存在")
            entity = initialize_entity(novel.template_fields.characters, args)
            novel.characters.append(entity)
            return ToolOutcome(
                message="人物资料已创建" if mode == "creation" else "人物已创建，尚未入场",
                data=entity.model_dump(mode="json"),
            )
        elif action.name == "edit_fields":
            return ToolOutcome(
                message="构思资料已更新" if mode == "creation" else "设定已修改，重置演绎仍保留",
                data=edit_fields(novel, scene, args),
            )
        elif action.name in ("enter_character", "remove_character"):
            character = resolve_character(novel, args.character)
            entering = action.name == "enter_character"
            if (character.id in present(scene)) == entering:
                raise ValueError("角色已在场" if entering else "角色不在场")
            emit(
                scene,
                "enter" if entering else "leave",
                args.reason or character.name + ("入场" if entering else "离场"),
                character.id,
            )
        return ToolOutcome(message="完成")

    async def run(self, mode, scene=None, instruction=""):
        novel = self.novel
        try:
            if mode in ("writing", "summary"):
                if mode == "writing":
                    data = scene_data(scene)
                    data["writing_mode"] = "rewrite" if scene.text else "generate"
                    data["previous_requirements"] = scene.writing_requirements
                    participants = {e.actor_id for e in scene.events if e.kind == "enter"}
                    data["characters"] = [
                        c.model_dump(mode="json")
                        for key, c in role_states(novel, scene).items()
                        if key in participants
                    ]
                    data["requirements"] = instruction
                    action = await self.call(mode, data, [])
                    if not action.text.strip():
                        raise ValueError("模型没有返回正文")
                    scene.text = action.text
                    scene.writing_requirements = instruction
                    next(c for c in novel.chapters if c.entity.id == scene.chapter_id).summary_stale = True
                else:
                    chapter = next(c for c in novel.chapters if c.entity.id == instruction)
                    scenes = [s for s in novel.scenes if s.chapter_id == chapter.entity.id]
                    data = {
                        "chapter": chapter.entity.name,
                        "scenes": [scene_data(s) for s in scenes],
                    }
                    action = await self.call(mode, data, [])
                    chapter.summary, chapter.summary_stale = action.text, False
            else:
                messages = scene.messages if scene else novel.creation_messages
                history = conversation_history(messages)
                while True:
                    data = (
                        gm_context(novel, scene)
                        if scene
                        else {
                            "title": novel.title,
                            "world": [e.model_dump(mode="json") for e in novel.world],
                            "characters": [e.model_dump(mode="json") for e in novel.characters],
                            "creation": [e.model_dump(mode="json") for e in novel.creation],
                        }
                    )
                    action = await self.call(mode, data, history)
                    if isinstance(action, StudioAction) and action.name == "reply":
                        # Native assistant text replies to the user; no tools means completion.
                        self.send_user_message(scene, action.text)
                        break
                    actions, text = self.begin_step(history, action)
                    self.send_user_message(scene, text)
                    restarted = False
                    rewind_result = None
                    for item in actions:
                        if restarted:
                            result = ToolOutcome(
                                success=False, message="状态已重置或回溯，本批次后续操作未执行"
                            )
                        else:
                            try:
                                result = await self.execute(mode, scene, item)
                            except ValueError as error:
                                result = ToolOutcome(success=False, message=str(error))
                        self.tool_result(history, item, result)
                        if result.success:
                            restarted = restarted or item.name in ("reset_performance", "rewind_performance")
                            if item.name == "rewind_performance":
                                rewind_result = (item, result)
                        await self.save_progress()
                    if restarted:
                        if rewind_result:
                            history = conversation_history(messages)
                            item, result = rewind_result
                            self.begin_step(history, item)
                            self.tool_result(history, item, result)
                        else:
                            history = []  # Discarded attempt results must not contaminate the new rehearsal.
        except BudgetReached:
            self.send_user_message(scene, "本轮达到调用预算，已暂停并保留进度。")
        except Exception as error:
            novel.error = str(error)
        finally:
            novel.busy = False
            novel.running = ""
            # Persist the idle status and keep the checkpoint at this final version.
            if novel.turn_checkpoint and mode in ("creation", "gm"):
                novel.turn_checkpoint.version = novel.version + 1
            await self.repository.save(novel)
            if self.gateway:
                await self.gateway.close()
