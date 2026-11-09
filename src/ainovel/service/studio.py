"""Ordinary GM/role tool loops; no graph or automatic writing pipeline."""

import json
import math
import re
from pathlib import Path
from uuid import UUID

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import TypeAdapter, ValidationError

from ainovel.core.entities import Entity, visible_entity
from ainovel.core.gm_context import GmContext, gm_context, legacy_gm_data
from ainovel.core.performance_timeline import changes_between, record_change, rewind
from ainovel.core.prose_edits import (
    apply_prose_edits,
    paragraph_ranges,
    prose_revision,
    read_prose,
    require_revision,
)
from ainovel.core.studio import (
    ContextMode,
    ContextTemplate,
    ContextTemplateSet,
    ConversationMessage,
    StageEvent,
    StudioAction,
    StudioDocumentation,
    StudioStep,
    ToolExecution,
    ToolOutcome,
    WritingMessage,
    gm_discussion_text,
)
from ainovel.core.studio_context import (
    document_variable_schema,
    entity_variable_schemas,
    json_pointer,
    variable_docs,
)
from ainovel.core.studio_state import (
    apply_patch,
    edit_self,
    emit,
    present,
    require_editable,
    require_writable_prose,
    reset,
    role_context,
    role_states,
)
from ainovel.core.studio_templates import initialize_entity
from ainovel.core.studio_tools import CreationEntityInput, StudioDecoder, TextDecoder, tool_schemas, tools_for
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

MODES = ("creation", "gm", "role", "writing", "writing_chat", "summary")
MAX_TOOL_FAILURES = 3
ENTITY_LIST = TypeAdapter(list[Entity])
DEFAULT_CONTEXT_SOURCE = "{{ data | tojson }}"
ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
ENV.globals.clear()
ENV.policies["json.dumps_kwargs"] = {**ENV.policies["json.dumps_kwargs"], "ensure_ascii": False}


def defaults():
    root = Path(__file__).resolve().parents[1] / "prompts"
    return {mode: (root / f"studio_{mode}.j2").read_text(encoding="utf-8") for mode in MODES}


def default_gm_context_source() -> str:
    return default_context_template("gm").source


def default_context_template(mode: ContextMode) -> ContextTemplate:
    name, version = {
        "gm": ("内置 GM 模板", "gm-v1"),
        "role": ("内置角色模板", "role-v1"),
        "writing": ("内置正文写作模板", "writing-v1"),
    }[mode]
    source = DEFAULT_CONTEXT_SOURCE
    if mode in ("gm", "writing"):
        path = Path(__file__).resolve().parents[1] / "prompts" / f"studio_{mode}_context.j2"
        source = path.read_text(encoding="utf-8")
    return ContextTemplate(id="default", name=name, source=source, schema_version=version)


def context_template_set(novel, mode: ContextMode) -> ContextTemplateSet:
    if mode == "gm":
        return ContextTemplateSet(
            templates=novel.gm_templates, active_id=novel.active_gm_template_id, active=novel.active_gm_template
        )
    return novel.context_template_sets.get(mode) or ContextTemplateSet()


def select_context_template(novel, mode: ContextMode, template_id: str | None) -> ContextTemplate:
    if template_id is None:
        if mode == "gm" and novel.gm_context_source is not None:
            return ContextTemplate(id="legacy", name="原有上下文模板", source=novel.gm_context_source)
        if novel.context_source.strip() != DEFAULT_CONTEXT_SOURCE:
            version = "shared-v1" if mode == "gm" else default_context_template(mode).schema_version
            return ContextTemplate(
                id="legacy", name="原有上下文模板", source=novel.context_source, schema_version=version
            )
        template_id = "default"
    if template_id == "default":
        return default_context_template(mode)
    template = next((item for item in context_template_set(novel, mode).templates if item.id == template_id), None)
    if template is None:
        raise ValueError("模板不存在")
    return template


def active_context_template(novel, mode: ContextMode) -> ContextTemplate:
    templates = context_template_set(novel, mode)
    return templates.active or select_context_template(novel, mode, templates.active_id)


def resolve_character(novel, name, scene=None):
    characters = role_states(novel, scene) if scene else {c.id: c for c in novel.characters}
    if name in characters:
        return characters[name]
    found = [c for c in characters.values() if c.name == name]
    if not found and scene:
        found = [characters[c.id] for c in novel.characters if c.id in characters and c.name == name]
    if len(found) != 1:
        raise ValueError("角色不存在或重名，请使用返回的已有 ID")
    return found[0]


def scene_data(scene):
    return {
        "scene": scene.entity.model_dump(mode="json"),
        "events": [
            {
                "kind": e.kind,
                "actor_id": e.actor_id,
                "text": e.text,
                **({"audience": e.audience} if e.kind in ("gm_direction", "self_edit") else {}),
            }
            for e in scene.events
        ],
        "text": scene.text,
    }


def conversation_history(messages):
    return [Message(role=m.role, content=m.content) for m in messages if m.content]


def gm_turn_messages(messages, draft=None):
    if draft is None and messages and messages[-1].role == "user":
        return messages[:-1], conversation_history(messages[-1:])
    return messages, [Message(role="user", content=draft)] if draft else []


def writing_scene_context(novel, scene):
    data = scene_data(scene)
    chapter = next(chapter for chapter in novel.chapters if chapter.entity.id == scene.chapter_id)
    data["chapter"] = chapter.entity.model_dump(mode="json")
    participants = {e.actor_id for e in scene.events if e.kind == "enter"}
    data["characters"] = [
        c.model_dump(mode="json") for key, c in role_states(novel, scene).items() if key in participants
    ]
    data["previous_requirements"] = scene.writing_requirements
    return data


def writing_context(novel, scene, requirements=""):
    return {
        **writing_scene_context(novel, scene),
        "writing_mode": "rewrite" if scene.text else "generate",
        "requirements": requirements,
    }


def writing_chat_context(novel, scene):
    return {
        "scene": scene.entity.name,
        "revision": prose_revision(scene.text),
        "paragraph_count": len(paragraph_ranges(scene.text)),
        "has_prose": bool(scene.text),
        "creation": [entity.model_dump(mode="json") for entity in novel.creation],
    }


def build_request(settings, novel, mode, data, history=None, *, context_template=None):
    override = novel.prompts.get(mode)
    source = override if override is not None else defaults()[mode]
    context_source = novel.context_source
    context_data = prompt_data = data
    legacy_layout = False
    if mode in ("role", "writing") or isinstance(data, GmContext):
        template = context_template or active_context_template(novel, mode)
        context_source = template.source
    if isinstance(data, GmContext):
        legacy_layout = template.schema_version == "shared-v1"
        context_data = data.model_dump(mode="json")
        prompt_data = context_data
        if legacy_layout or override is not None:
            legacy = legacy_gm_data(data)
            if override is not None:
                prompt_data = legacy
            if legacy_layout:
                context_data = legacy
                history = [Message(role=m.role, content=m.text) for m in data.discussion] + (history or [])
    try:
        role_prompt = ENV.from_string(source).render(data=prompt_data)
        context = ENV.from_string(context_source).render(data=context_data)
    except TemplateError as error:
        raise ValueError(f"Jinja 模板无法渲染：{error}") from error
    docs = novel.documentation.get(mode) or StudioDocumentation()
    schemas = tool_schemas(mode, docs.tool_descriptions)
    documentation = {"tool_result_schema": ToolOutcome.model_json_schema()}
    if schemas:
        documentation["tool_descriptions"] = {item["name"]: item["description"] for item in schemas}
    if docs.variable_detail != "off":
        variables = {
            path: field for path, field in variable_docs(mode, shared=legacy_layout).items()
            if docs.variable_detail == "full" or path.count(".") == 1 or path in docs.variable_descriptions
        }
        for parts, field in entity_variable_schemas(context_data):
            path = "data" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts)
            variables[path] = field
        documentation["variable_schema"] = {
            path: {**field, "description": docs.variable_descriptions.get(path) or field["description"]}
            for path, field in variables.items()
        }
        if any(schema["name"] == "edit_fields" for schema in schemas):
            document_model = CreationEntityInput if mode == "creation" else Entity
            documentation["document_schema"] = document_model.model_json_schema()
    body = ChatBody(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        messages=[
            Message(role="system", content=role_prompt),
            Message(
                role="system",
                content=json.dumps(
                    documentation, ensure_ascii=False
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


def estimate_text_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 3.5)


def estimate(request):
    text = request.body.model_dump_json(exclude_none=True)
    return {
        "input_tokens": estimate_text_tokens(text),
        "method": "中英文字符混合估算，实际以供应商计费为准",
        "characters": len(text),
    }


def edit_fields(novel, scene, args):
    scope, target = args.scope, args.target
    if scope == "characters":
        if not target:
            raise ValueError("新建角色使用 create_character；修改角色请指定目标")
        entity = resolve_character(novel, target, scene)
        if scene and scene.character_patches.get(entity.id):
            raise ValueError("该角色已有演绎变化，请先重置演绎再修改其设定")
        original = entity.model_dump(mode="json")
        candidate = Entity.model_validate(apply_patch(original, args.operations))
        if candidate.id != entity.id:
            raise ValueError("不能修改内部 ID")
        characters = role_states(novel, scene).values() if scene else novel.characters
        if any(c.id != entity.id and c.name == candidate.name for c in characters):
            raise ValueError("角色名字重复")
        if scene:
            patches = changes_between(original, candidate.model_dump(mode="json"))
            if patches:
                scene.settings_patches.setdefault(entity.id, []).extend(patches)
        else:
            novel.characters[novel.characters.index(entity)] = candidate
        return candidate.model_dump(mode="json")
    collection = getattr(novel, scope)
    if target:
        matches = [
            e
            for e in collection
            if (e.entity if scope == "chapters" else e).id == target
            or (e.entity if scope == "chapters" else e).name == target
        ]
        if len(matches) != 1:
            raise ValueError("目标不存在或重名")
        item = matches[0]
        if scope == "chapters" and scene and item.entity.id != scene.chapter_id:
            raise ValueError("只能修改当前章节资料")
        entity = item.entity if scope == "chapters" else item
        candidate = Entity.model_validate(apply_patch(entity.model_dump(mode="json"), args.operations))
        if candidate.id != entity.id:
            raise ValueError("不能修改内部 ID")
        if scope == "chapters":
            item.entity = candidate
        else:
            collection[collection.index(item)] = candidate
        return candidate.model_dump(mode="json")
    if scope not in ("world", "creation"):
        raise ValueError("此类别需要指定目标")
    values = apply_patch([e.model_dump(mode="json") for e in collection], args.operations)
    known = {e.id for e in collection}
    if isinstance(values, list):
        values = [
            {key: item for key, item in value.items() if key != "id"}
            if isinstance(value, dict) and (not isinstance(value.get("id"), str) or value["id"] not in known)
            else value
            for value in values
        ]
    entities = ENTITY_LIST.validate_python(values)
    if len({e.id for e in entities}) != len(entities):
        raise ValueError("实体 ID 重复")
    setattr(novel, scope, entities)
    return [e.model_dump(mode="json") for e in entities]


class BudgetReached(Exception):
    pass


class ToolRetryLimit(Exception):
    pass


def tool_validation_message(name: str, error: ValidationError) -> str:
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    details = "；".join(f"{json_pointer(issue['loc']) or '根路径'} {issue['msg']}" for issue in issues)
    if name != "edit_fields":
        return f"工具参数或资料校验失败。{details}"
    hint = ""
    if any(
        issue["type"] == "extra_forbidden"
        and issue["loc"] and issue["loc"][-1] in ("value", "visibility")
        and all(isinstance(part, int) for part in issue["loc"][:-1])
        for issue in issues
    ):
        hint = "描述内容应放在资料的 description 列表中，描述条目的 value 是字符串数组。"
    return f"校验失败，整批补丁未保存。{details}。{hint}请修正后重新提交。"


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

    def send_user_message(self, scene, text, *, writing=False, model_reply=False):
        """Store each actual user-facing message without content-based suppression."""
        if scene is not None and not writing:
            text = gm_discussion_text(text)
        if not text.strip():
            return None
        kind = WritingMessage if writing else ConversationMessage
        message = kind(role="assistant", content=text, model_reply=model_reply)
        self.messages(scene, writing=writing).append(message)
        return message

    def messages(self, scene, *, writing=False):
        if writing:
            return scene.writing_messages
        return scene.messages if scene else self.novel.creation_messages

    @staticmethod
    def add_tool_executions(scene, reply, executions):
        if isinstance(reply, StageEvent):
            scene.event_tool_executions.setdefault(reply.id, []).extend(executions)
        else:
            reply.tool_executions.extend(executions)

    def attach_pending_tools(self, scene, reply, pending):
        if reply is not None and pending.tool_executions:
            self.add_tool_executions(scene, reply, pending.tool_executions)
            self.messages(scene, writing=isinstance(pending, WritingMessage)).remove(pending)
            pending.tool_executions.clear()

    def record_tool_execution(self, scene, messages, reply, pending, execution):
        if reply is not None:
            self.attach_pending_tools(scene, reply, pending)
            self.add_tool_executions(scene, reply, [execution])
        else:
            if not pending.tool_executions:
                messages.append(pending)
            pending.tool_executions.append(execution)

    async def save_progress(self):
        if self.novel.turn_checkpoint and self.novel.running.split(" · ")[0] in (
            "creation",
            "gm",
            "role",
            "writing_chat",
        ):
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
        if mode == "writing_chat":
            return StudioAction(name="reply", text="当前使用离线模拟模型。")
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
        if mode == "gm" and data.current_scene.present and "schedule_characters" not in calls:
            return StudioAction(
                name="schedule_characters",
                arguments={"characters": [character.id for character in data.current_scene.present]},
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
                "GM 调度提示词：" + character.name,
                lambda n, s: emit(s, "gm_direction", direction, character.id, audience=[character.id]),
                actor_id=character.id,
            )
            direction_id = event.id
        history = []
        pending = ConversationMessage(role="assistant", content="")
        failures = {}
        while True:
            data = role_context(self.novel, scene, character.id, current_direction_id=direction_id)
            data["direction"] = direction
            action = await self.call("role", data, history)
            final = isinstance(action, StudioAction) and action.name == "reply"
            reply = self.send_role_message(scene, character, action.text) if final or action.text else None
            self.attach_pending_tools(scene, reply, pending)
            if final:
                scene.event_tool_executions.setdefault(reply.id, [])
                await self.save_progress()
                return action.text
            departed, _ = await self.execute_reply_tools(
                "role", scene, history, action, pending, reply, failures, character=character,
            )
            if departed:
                return character.name + "已离场"

    def send_role_message(self, scene, character, text):
        return self.change(
            scene, "role_reply", character.name + "发言",
            lambda n, s: emit(s, "role", text, character.id), origin="role", actor_id=character.id,
        )

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
                            name=item.name,
                            arguments=(
                                item.raw_arguments if item.raw_arguments is not None
                                else json.dumps(item.arguments, ensure_ascii=False)
                            ),
                        ),
                    )
                    for item in actions
                ],
            )
        )
        return actions

    @staticmethod
    def tool_result(history, action, result):
        exclude = {"variable_schema"} if result.variable_schema is None else None
        history.append(Message(role="tool", tool_call_id=action.call_id, content=result.model_dump_json(exclude=exclude)))

    async def execute_reply_tools(self, mode, scene, history, step, pending, reply, failures, *, character=None):
        actions = self.begin_step(history, step)
        messages = self.messages(scene, writing=mode == "writing_chat")
        actor = character.name if character else ""
        stopped, rewind_result, last_output = False, None, None
        retry_limit = None
        for item in actions:
            message_count = len(messages)
            event_count = len(scene.events) if scene else 0
            try:
                if retry_limit:
                    result = ToolOutcome(success=False, message="本轮已达到工具失败次数上限，未执行。")
                elif stopped:
                    result = ToolOutcome(success=False, message=(
                        "角色已离场，未执行" if mode == "role" else "状态已重置或回溯，本批次后续操作未执行"
                    ))
                else:
                    result = await self.execute(mode, scene, item, character=character)
            except ValidationError as error:
                result = ToolOutcome(success=False, message=tool_validation_message(item.name, error))
            except ValueError as error:
                message = str(error)
                if item.name == "edit_fields":
                    message = f"整批补丁未保存。{message} 请修正后重新提交。"
                result = ToolOutcome(success=False, message=message)
            except Exception as error:
                execution = ToolExecution(
                    name=item.name, actor=actor,
                    outcome=ToolOutcome(success=False, message=str(error) or "工具执行中断"),
                )
                self.record_tool_execution(scene, messages, reply, pending, execution)
                raise
            self.tool_result(history, item, result)
            execution = ToolExecution(name=item.name, actor=actor, outcome=result)
            output = None
            if item.name != "schedule_characters":  # Nested role replies own their own records.
                if len(messages) > message_count:
                    output = messages[-1]
                elif scene and len(scene.events) > event_count:
                    output = scene.events[-1]
            if output is not None:
                last_output = output
            target = reply or output or (last_output if stopped or retry_limit else None)
            self.record_tool_execution(scene, messages, target, pending, execution)
            if reply is not None and output is not None:
                self.add_tool_executions(scene, output, [execution])
            if result.success:
                failures.pop(item.name, None)
                if item.name in ("leave", "reset_performance", "rewind_performance"):
                    stopped = True
                    if item.name == "rewind_performance":
                        rewind_result = (item, result)
            elif not stopped and retry_limit is None:
                failures[item.name] = failures.get(item.name, 0) + 1
                if failures[item.name] >= MAX_TOOL_FAILURES:
                    retry_limit = ToolRetryLimit(
                        f"工具 {item.name} 连续失败 {MAX_TOOL_FAILURES} 次，本轮已停止。{result.message}"
                    )
            await self.save_progress()
        if retry_limit:
            raise retry_limit
        return stopped, rewind_result

    async def execute(self, mode, scene, action, *, character=None):
        if mode == "gm":
            require_editable(self.novel, scene)
        schema = tools_for(mode).get(action.name)
        if schema is None:
            raise ValueError("当前模式没有该工具权限")
        args = (
            schema.model_validate_json(action.raw_arguments) if action.raw_arguments is not None
            else schema.model_validate(action.arguments)
        )
        novel = self.novel
        if mode == "role":
            return self.execute_role_tool(scene, character, action.name, args)
        if mode == "writing_chat":
            return self.execute_writing(scene, action.name, args)
        if action.name == "rewind_performance":
            data = rewind(self.novel, scene, steps=args.steps, target=args.target)
            return ToolOutcome(
                message="已从起始状态重放到选定位置；后续操作不再生效，用户讨论保留", data=data
            )
        if action.name == "schedule_characters":
            replies = []
            for item in args.characters:
                name, direction = (
                    (item, args.direction) if isinstance(item, str) else (item.character, item.direction)
                )
                character = resolve_character(novel, name, scene)
                if character.id not in present(scene):
                    replies.append({"character": character.name, "skipped": "不在场，未运行"})
                    continue
                reply = await self.role(scene, character, direction)
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

    def execute_role_tool(self, scene, character, name, args):
        if name == "leave":
            self.change(
                scene, "leave", character.name + "离场",
                lambda n, s: emit(s, "leave", args.reason or character.name + "离场", character.id),
                origin="role", actor_id=character.id,
            )
            return ToolOutcome(message="已离场")
        edited = self.change(
            scene, "edit_self", character.name + "更新自身字段",
            lambda n, s: edit_self(n, s, character.id, args.operations), origin="role", actor_id=character.id,
        )
        own = visible_entity(role_states(self.novel, scene)[character.id], own=True).model_dump(mode="json")
        return ToolOutcome(
            message="自身状态已修改，仅本次演绎生效" if edited else "字段值未发生变化",
            variable_schema=document_variable_schema(own),
        )

    def execute_writing(self, scene, name, args):
        novel = self.novel
        if novel.deleted or not novel.created or not scene.confirmed:
            raise ValueError("只能操作已确认的演绎")
        if name == "read_prose":
            return ToolOutcome(data=read_prose(scene.text, args.start, args.count, args.query))
        if name == "read_scene":
            data = writing_scene_context(novel, scene)
            data.pop("text")  # Prose is read separately, in the requested range.
            return ToolOutcome(data=data)
        if name == "edit_fields":
            active = novel.scenes[-1]
            if active.confirmed:
                result = edit_fields(novel, None, args)
            else:
                result = self.change(
                    active,
                    "writing_edit_fields",
                    "写作助手修改创作资料",
                    lambda n, s: edit_fields(n, None, args),
                    origin="writer",
                )
            return ToolOutcome(message="创作资料已更新", data=result, variable_schema=document_variable_schema(result))

        require_writable_prose(novel, scene)
        if name == "edit_prose":
            if not scene.text:
                raise ValueError("本幕还没有正文，请先提交完整正文")
            text, changes = apply_prose_edits(scene.text, args.revision, args.edits)
            count = sum(change.count for change in changes)
            message = f"正文已修改 {count} 处"
        else:
            require_revision(scene.text, args.revision)
            text, changes = args.text, []
            message = "已保存整幕正文"
        if text == scene.text:
            return ToolOutcome(message="正文没有变化", data={"revision": prose_revision(scene.text)})
        if not text.strip():
            raise ValueError("正文不能为空")
        instruction = next(m.content for m in reversed(scene.writing_messages) if m.role == "user")
        self.save_prose(scene, text, instruction)
        scene.writing_messages.append(WritingMessage(role="assistant", content=message, changes=changes))
        return ToolOutcome(message=message, data={"revision": prose_revision(scene.text)})

    def save_prose(self, scene, text, instruction):
        scene.text = text
        scene.writing_requirements = instruction
        next(c for c in self.novel.chapters if c.entity.id == scene.chapter_id).summary_stale = True

    def execute_atomic(self, mode, novel, scene, action, args):
        if action.name == "say_to_user":
            text = args.text
            sent = self.send_user_message(scene, text) is not None
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
            if any(e.name == args.name for e in novel.characters) or (
                scene and any(e.name == args.name for e in role_states(novel, scene).values())
            ):
                raise ValueError("角色名字已存在")
            entity = initialize_entity(novel.template_fields.characters, args)
            novel.characters.append(entity)
            return ToolOutcome(
                message="人物资料已创建" if mode == "creation" else "人物已创建，尚未入场",
                data=entity.model_dump(mode="json"),
            )
        elif action.name == "edit_fields":
            result = edit_fields(novel, scene, args)
            return ToolOutcome(
                message="构思资料已更新" if mode == "creation" else "设定已修改，重置演绎仍保留",
                data=result,
                variable_schema=document_variable_schema(result),
            )
        elif action.name in ("enter_character", "remove_character"):
            character = resolve_character(novel, args.character, scene)
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
        writing_chat = mode == "writing_chat"
        pending = (WritingMessage if writing_chat else ConversationMessage)(role="assistant", content="")
        failures = {}
        try:
            if mode in ("writing", "summary"):
                if mode == "writing":
                    data = writing_context(novel, scene, instruction)
                    action = await self.call(mode, data, [])
                    if not action.text.strip():
                        raise ValueError("模型没有返回正文")
                    self.save_prose(scene, action.text, instruction)
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
                messages = self.messages(scene, writing=writing_chat)
                discussion, active = gm_turn_messages(messages) if mode == "gm" else ([], [])
                history = active.copy() if mode == "gm" else conversation_history(messages)
                while True:
                    data = (
                        writing_chat_context(novel, scene)
                        if writing_chat
                        else gm_context(novel, scene, discussion)
                        if scene
                        else {
                            "title": novel.title,
                            "world": [e.model_dump(mode="json") for e in novel.world],
                            "characters": [e.model_dump(mode="json") for e in novel.characters],
                            "creation": [e.model_dump(mode="json") for e in novel.creation],
                        }
                    )
                    action = await self.call(mode, data, history)
                    final = isinstance(action, StudioAction) and action.name == "reply"
                    reply = self.send_user_message(scene, action.text, writing=writing_chat, model_reply=final)
                    self.attach_pending_tools(scene, reply, pending)
                    if final:
                        break
                    restarted, rewind_result = await self.execute_reply_tools(
                        mode, scene, history, action, pending, reply, failures,
                    )
                    if restarted:
                        failures.clear()
                        if rewind_result:
                            history = active.copy()
                            item, result = rewind_result
                            self.begin_step(history, item)
                            self.tool_result(history, item, result)
                        else:
                            history = active.copy()
        except BudgetReached:
            reply = self.send_user_message(scene, "本轮达到调用预算，已暂停并保留进度。", writing=writing_chat)
            self.attach_pending_tools(scene, reply, pending)
        except ToolRetryLimit as error:
            reply = self.send_user_message(scene, str(error), writing=writing_chat)
            self.attach_pending_tools(scene, reply, pending)
        except Exception as error:
            novel.error = str(error)
        finally:
            novel.busy = False
            novel.running = ""
            # Persist the idle status and keep the checkpoint at this final version.
            if novel.turn_checkpoint and mode in ("creation", "gm", "writing_chat"):
                novel.turn_checkpoint.version = novel.version + 1
            await self.repository.save(novel)
            if self.gateway:
                await self.gateway.close()
