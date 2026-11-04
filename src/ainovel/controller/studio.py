"""Version-checked workspace operations and asynchronous, single-owner GM turns."""

from copy import deepcopy
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks
from jinja2 import TemplateError
from pydantic import Field

from ainovel.core.base import Model
from ainovel.core.entities import Attribute, Description, EntityInput
from ainovel.core.errors import ConflictError
from ainovel.core.studio import (
    Compression,
    ConversationMessage,
    StudioBranch,
    StudioChapter,
    StudioNovel,
    StudioScene,
    ToolOutcome,
)
from ainovel.core.studio_state import fork, present, require_editable, reset, role_states
from ainovel.core.studio_templates import (
    StudioFieldTemplate,
    TemplateFields,
    entity_fields,
    initialize_entity,
)
from ainovel.core.studio_tools import Edit, tool_schemas
from ainovel.core.studio_turns import checkpoint, restore_turn
from ainovel.mapper.studio import StudioRepository
from ainovel.service.studio import (
    ENV,
    MODES,
    StudioRunner,
    build_request,
    conversation_history,
    defaults,
    edit_fields,
    estimate,
    gm_context,
)


class VersionRequest(Model):
    version: int


class SendRequest(VersionRequest):
    scene_id: str = ""
    message: str = Field(min_length=1)


class TurnRequest(VersionRequest):
    action: Literal["retry", "edit", "withdraw"]
    scene_id: str = ""
    message_id: str
    message: str = ""
    preserve_state: bool = Field(
        default=False, description="仅用于无检查点旧消息：明确同意保留当前资料和演绎，只替换或撤回聊天记录。"
    )


class ControlRequest(VersionRequest):
    action: Literal[
        "confirm_creation",
        "new_scene",
        "confirm_scene",
        "reset",
        "branch",
        "write",
        "undo_prose",
        "finish_chapter",
        "delete",
        "restore",
        "enter",
        "leave",
    ]
    scene_id: str = ""
    branch_id: str = ""
    name: str = ""
    new_chapter: bool = False
    instruction: str = ""


class EditRequest(VersionRequest):
    scope: Literal["world", "characters", "creation", "chapters", "scenes"]
    target: str = ""
    entity: EntityInput | None = None
    delete: bool = False
    title: str | None = None


class SettingsRequest(VersionRequest):
    compression: Compression
    prompts: dict[str, str]
    context_source: str


class EstimateRequest(Model):
    scene_id: str = ""
    branch_id: str = ""
    message: str = ""
    compression: Compression
    prompts: dict[str, str]
    context_source: str


class NovelCard(Model):
    id: str
    title: str
    created: bool
    deleted: bool
    busy: bool


class SaveFieldTemplate(Model):
    id: str | None = None
    version: int = 0
    name: str = Field(min_length=1, max_length=160)
    fields: TemplateFields


class ApplyFieldTemplate(VersionRequest):
    template_id: str


class ExportFieldTemplate(VersionRequest):
    name: str = Field(min_length=1, max_length=160)


def router(store, settings):
    api = APIRouter(prefix="/api/studio", tags=["角色演绎工作区"])
    repository = StudioRepository(store)

    async def writable(key, version):
        novel = await repository.get(key)
        if novel.version != version or novel.busy:
            raise ConflictError("资料版本已变化或 GM 正在运行，请等待并刷新")
        return novel

    def find_scene(novel, key):
        scene = next((s for s in novel.scenes if s.entity.id == key), None)
        if scene is None:
            raise ValueError("幕不存在")
        return scene

    async def launch(novel, tasks, mode, scene=None, instruction=""):
        novel.busy, novel.run_id, novel.error = True, str(uuid4()), ""
        novel.running = mode
        if mode in ("creation", "gm") and novel.turn_checkpoint:
            novel.turn_checkpoint.version = novel.version + 1
        await repository.save(novel)
        tasks.add_task(StudioRunner(novel, repository, settings).run, mode, scene, instruction)

    @api.get("/contracts")
    async def contracts():
        return {
            "tools": {mode: tool_schemas(mode) for mode in MODES},
            "returns": ToolOutcome.model_json_schema(),
            "entities": {
                cls.__name__: cls.model_json_schema() for cls in (EntityInput, Description, Attribute)
            },
            "prompts": defaults(),
            "context_source": "{{ data | tojson }}",
        }

    @api.get("/novels", response_model=list[NovelCard])
    async def novels():
        return [
            NovelCard(**{k: getattr(n, k) for k in NovelCard.model_fields}) for n in await repository.list()
        ]

    @api.get("/templates", response_model=list[StudioFieldTemplate])
    async def templates():
        return await repository.templates()

    @api.post("/templates", response_model=StudioFieldTemplate)
    async def save_template(request: SaveFieldTemplate):
        value = StudioFieldTemplate(
            name=request.name,
            fields=request.fields,
            version=request.version,
            **({"id": request.id} if request.id else {}),
        )
        return await repository.save_template(value, create=request.id is None)

    @api.delete("/templates/{key}")
    async def remove_template(key: str, version: int):
        await repository.delete_template(key, version)
        return {"deleted": True}

    @api.post(
        "/novels/{key}/template",
        response_model=StudioNovel,
        response_model_exclude={"turn_checkpoint": {"before"}},
    )
    async def apply_template(key: str, request: ApplyFieldTemplate):
        novel = await writable(key, request.version)
        if novel.created or novel.deleted:
            raise ValueError("字段模板用于创建前初始化；创建后直接修改本书资料")
        template = await repository.template(request.template_id)
        novel.template_fields = template.fields.model_copy(deep=True)
        novel.field_template_name = template.name
        # Add missing defaults only: applying a template never overwrites the conversation's edits.
        for scope in ("world", "creation"):
            prototype = getattr(novel.template_fields, scope)
            collection = getattr(novel, scope)
            if collection:
                first = collection[0]
                initialized = initialize_entity(prototype, entity_fields(first))
                initialized.id = first.id
                collection[0] = initialized
            elif prototype.description or prototype.special_attributes:
                collection.append(
                    initialize_entity(prototype, EntityInput(name=prototype.name, type=prototype.type))
                )
        for index, character in enumerate(novel.characters):
            initialized = initialize_entity(novel.template_fields.characters, entity_fields(character))
            initialized.id = character.id
            novel.characters[index] = initialized
        await repository.save(novel)
        return novel

    @api.post("/novels/{key}/template/export", response_model=StudioFieldTemplate)
    async def export_template(key: str, request: ExportFieldTemplate):
        novel = await writable(key, request.version)
        fields = novel.template_fields.model_copy(deep=True)
        for scope in ("world", "creation"):
            collection = getattr(novel, scope)
            if collection:
                setattr(fields, scope, entity_fields(collection[0]))
            else:
                previous = getattr(fields, scope)
                setattr(fields, scope, EntityInput(name=previous.name, type=previous.type))
        characters = (
            role_states(novel, novel.scenes[-1]) if novel.scenes else {c.id: c for c in novel.characters}
        )
        if characters:
            fields.characters = entity_fields(next(iter(characters.values())))
        if novel.chapters:
            fields.chapters = entity_fields(novel.chapters[-1].entity)
        if novel.scenes:
            fields.scenes = entity_fields(novel.scenes[-1].entity)
        return await repository.save_template(
            StudioFieldTemplate(name=request.name, fields=fields), create=True
        )

    @api.post("/novels", response_model=StudioNovel, response_model_exclude={"turn_checkpoint": {"before"}})
    async def create():
        return await repository.create()

    @api.get(
        "/novels/{key}", response_model=StudioNovel, response_model_exclude={"turn_checkpoint": {"before"}}
    )
    async def novel(key: str):
        return await repository.get(key)

    @api.post(
        "/novels/{key}/message",
        response_model=StudioNovel,
        response_model_exclude={"turn_checkpoint": {"before"}},
    )
    async def message(key: str, request: SendRequest, tasks: BackgroundTasks):
        novel = await writable(key, request.version)
        if novel.deleted:
            raise ValueError("小说在回收站中")
        scene = find_scene(novel, request.scene_id) if novel.created else None
        if scene:
            require_editable(novel, scene)
        if not request.message.strip():
            raise ValueError("消息不能为空")
        user_message = ConversationMessage(role="user", content=request.message)
        checkpoint(novel, scene, user_message.id)
        (scene.messages if scene else novel.creation_messages).append(user_message)
        await launch(novel, tasks, "gm" if scene else "creation", scene)
        return novel

    @api.post(
        "/novels/{key}/turn",
        response_model=StudioNovel,
        response_model_exclude={"turn_checkpoint": {"before"}},
    )
    async def turn(key: str, request: TurnRequest, tasks: BackgroundTasks):
        novel = await writable(key, request.version)
        if request.action == "edit" and not request.message.strip():
            raise ValueError("修改后的消息不能为空")
        content = restore_turn(
            novel, request.scene_id, request.message_id, preserve_state=request.preserve_state
        )
        if request.action == "withdraw":
            await repository.save(novel)
        else:
            scene = find_scene(novel, request.scene_id) if novel.created else None
            user_message = ConversationMessage(
                role="user", content=request.message if request.action == "edit" else content
            )
            checkpoint(novel, scene, user_message.id)
            (scene.messages if scene else novel.creation_messages).append(user_message)
            await launch(novel, tasks, "gm" if scene else "creation", scene)
        return novel

    @api.post(
        "/novels/{key}/control",
        response_model=StudioNovel,
        response_model_exclude={"turn_checkpoint": {"before"}},
    )
    async def control(key: str, request: ControlRequest, tasks: BackgroundTasks):
        novel = await writable(key, request.version)
        action = request.action
        if novel.deleted and action != "restore":
            raise ValueError("请先恢复小说")
        if action in ("delete", "restore"):
            novel.deleted = action == "delete"
        elif action == "confirm_creation":
            if novel.created:
                raise ValueError("小说已经创建")
            if not any(m.role == "user" for m in novel.creation_messages) or not any(
                m.role == "assistant" for m in novel.creation_messages
            ):
                raise ValueError("请先与 AI 交流构思，整理设定后再确认创建")
            novel.created = True
        elif action == "new_scene":
            if not novel.created or (novel.scenes and not novel.scenes[-1].confirmed):
                raise ValueError("请先确认创建或确认上一幕")
            if request.new_chapter or not novel.chapters:
                if novel.chapters and not novel.chapters[-1].completed:
                    raise ValueError("请先完成上一章并生成摘要，再开启新章")
                novel.chapters.append(
                    StudioChapter(
                        entity=initialize_entity(
                            novel.template_fields.chapters,
                            EntityInput(name=f"第 {len(novel.chapters) + 1} 章", type="chapter"),
                        )
                    )
                )
            elif novel.chapters[-1].completed:
                raise ValueError("本章已完成，请开启新章")
            novel.scenes.append(
                StudioScene(
                    entity=initialize_entity(
                        novel.template_fields.scenes,
                        EntityInput(name=request.name or f"第 {len(novel.scenes) + 1} 幕", type="scene"),
                    ),
                    chapter_id=novel.chapters[-1].entity.id,
                )
            )
        elif action == "branch":
            if request.branch_id:
                archived = next((b for b in novel.branches if b.id == request.branch_id), None)
                if not archived:
                    raise ValueError("原路线不存在")
                old_scenes, old_chapters = deepcopy(novel.scenes), deepcopy(novel.chapters)
                novel.scenes, novel.chapters = deepcopy(archived.scenes), deepcopy(archived.chapters)
                fork(novel, request.scene_id)
                novel.branches[-1].scenes, novel.branches[-1].chapters = old_scenes, old_chapters
            else:
                fork(novel, request.scene_id)
            novel.scenes[-1].messages = []
        else:
            scene = find_scene(novel, request.scene_id)
            chapter = next(c for c in novel.chapters if c.entity.id == scene.chapter_id)
            if action == "reset":
                require_editable(novel, scene)
                reset(scene)
            elif action in ("enter", "leave"):
                require_editable(novel, scene)
                from ainovel.core.studio import StudioAction

                await StudioRunner(novel, repository, settings).execute(
                    "gm",
                    scene,
                    StudioAction(
                        name="enter_character" if action == "enter" else "remove_character",
                        arguments={"character": request.name},
                    ),
                )
            elif action == "confirm_scene":
                require_editable(novel, scene)
                scene.confirmed = True
                if len(novel.scenes) % novel.compression.snapshot_interval == 0:
                    scene.character_snapshot = role_states(novel, scene)
                chapter.summary_stale = True
            elif action == "write":
                if not scene.confirmed:
                    raise ValueError("请先确认演绎")
                index = novel.scenes.index(scene)
                if not scene.text and any(not s.text for s in novel.scenes[:index]):
                    raise ValueError("请按幕顺序生成正文")
                await launch(novel, tasks, "writing", scene, request.instruction)
                return novel
            elif action == "undo_prose":
                written = [s for s in novel.scenes if s.text]
                if not written or written[-1].entity.id != scene.entity.id:
                    raise ValueError("只能按顺序回退最后一份正文")
                if scene.entity.id != novel.scenes[-1].entity.id:
                    novel.branches.append(
                        StudioBranch(
                            name=f"正文回退前路线 {len(novel.branches) + 1}",
                            scenes=deepcopy(novel.scenes),
                            chapters=deepcopy(novel.chapters),
                        )
                    )
                    index = novel.scenes.index(scene)
                    novel.scenes = novel.scenes[: index + 1]
                    kept = {s.chapter_id for s in novel.scenes}
                    novel.chapters = [c for c in novel.chapters if c.entity.id in kept]
                scene.text = ""
                scene.confirmed = False
                scene.character_snapshot = None
                chapter.completed = False
                chapter.summary_stale = True
            elif action == "finish_chapter":
                scenes = [s for s in novel.scenes if s.chapter_id == chapter.entity.id]
                if not scenes or any(not s.confirmed or not s.text for s in scenes):
                    raise ValueError("请先确认本章所有幕并生成正文")
                chapter.completed = True
                await launch(novel, tasks, "summary", None, chapter.entity.id)
                return novel
        await repository.save(novel)
        return novel

    @api.post(
        "/novels/{key}/edit",
        response_model=StudioNovel,
        response_model_exclude={"turn_checkpoint": {"before"}},
    )
    async def edit(key: str, request: EditRequest):
        novel = await writable(key, request.version)
        if novel.deleted:
            raise ValueError("小说在回收站中")
        scene = novel.scenes[-1] if novel.scenes else None
        if novel.created and scene:
            require_editable(novel, scene)
        if request.title is not None:
            novel.title = request.title.strip() or "未命名小说"
        elif request.scope == "characters" and not request.target:
            if request.delete or request.entity is None:
                raise ValueError("缺少人物资料")
            if any(c.name == request.entity.name for c in novel.characters):
                raise ValueError("角色名字重复")
            novel.characters.append(initialize_entity(novel.template_fields.characters, request.entity))
        elif request.entity is not None and request.target:
            # Replace editable fields only, never the system ID.
            operations = [
                dict(op="replace", path="/" + k, value=v)
                for k, v in request.entity.model_dump(mode="json").items()
            ]
            edit_fields(novel, scene, Edit(scope=request.scope, target=request.target, operations=operations))
        elif request.scope in ("world", "creation"):
            collection = getattr(novel, request.scope)
            if request.delete:
                index = next((i for i, e in enumerate(collection) if e.id == request.target), None)
                if index is None:
                    raise ValueError("实体不存在")
                operations = [dict(op="remove", path=f"/{index}")]
            elif request.entity:
                operations = [dict(op="add", path="/-", value=request.entity.model_dump(mode="json"))]
            else:
                raise ValueError("缺少实体内容")
            edit_fields(novel, scene, Edit(scope=request.scope, operations=operations))
        else:
            raise ValueError("角色删除会破坏历史状态，请通过分支调整出场，不删除历史实体")
        await repository.save(novel)
        return novel

    @api.post(
        "/novels/{key}/settings",
        response_model=StudioNovel,
        response_model_exclude={"turn_checkpoint": {"before"}},
    )
    async def save_settings(key: str, request: SettingsRequest):
        novel = await writable(key, request.version)
        if set(request.prompts) - set(MODES):
            raise ValueError("未知提示词类型")
        try:
            for source in [*request.prompts.values(), request.context_source]:
                ENV.parse(source)
        except TemplateError as error:
            raise ValueError(f"Jinja 模板语法错误：{error}") from error
        novel.compression, novel.prompts, novel.context_source = (
            request.compression,
            request.prompts,
            request.context_source,
        )
        await repository.save(novel)
        return novel

    @api.get("/novels/{key}/preview")
    async def preview(key: str, scene_id: str = "", message: str = "", branch_id: str = ""):
        novel = await repository.get(key)
        return preview_data(novel, scene_id, message, branch_id)

    @api.post("/novels/{key}/estimate")
    async def preview_edits(key: str, request: EstimateRequest):
        novel = await repository.get(key)
        novel.compression = request.compression
        novel.prompts = request.prompts
        novel.context_source = request.context_source
        return preview_data(novel, request.scene_id, request.message, request.branch_id)

    def preview_data(novel, scene_id, message, branch_id=""):
        if branch_id:
            branch = next((b for b in novel.branches if b.id == branch_id), None)
            if branch is None:
                raise ValueError("历史路线不存在")
            novel.scenes = branch.scenes
            novel.chapters = branch.chapters
        scene = find_scene(novel, scene_id) if novel.created and scene_id else None
        mode = "gm" if scene else "creation"
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
        history = scene.messages if scene else novel.creation_messages
        from ainovel.llm.schemas import Message

        request = build_request(settings, novel, mode, data, conversation_history(history, mode))
        if message:
            request.body.messages.extend(conversation_history([Message(role="user", content=message)], mode))
        return {
            **estimate(request),
            "messages": [m.model_dump(mode="json") for m in request.body.messages],
            "tools": tool_schemas(mode),
            "characters": {
                key: value.model_dump(mode="json") for key, value in role_states(novel, scene).items()
            }
            if scene
            else {},
            "present": present(scene) if scene else [],
        }

    @api.delete("/novels/{key}")
    async def purge(key: str, version: int):
        await repository.purge(key, version)
        return {"deleted": True}

    return api, repository
