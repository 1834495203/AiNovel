import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from ainovel.bootstrap.runtime import Runtime
from ainovel.controller.assets import WebAssets
from ainovel.core.characters import Snapshot
from ainovel.core.config import Settings
from ainovel.core.discussion import DiscussionSession
from ainovel.core.domain import (
    CommandSubmission,
    CommandView,
    EventView,
    Health,
    Model,
    PromptPreviewMessage,
    PromptTemplateLibrary,
    PromptTemplatePreview,
    PromptTemplatePreviewRequest,
    PurgeResult,
    Revision,
    SetupDraft,
    StorySummary,
    StoryView,
    UndoState,
)
from ainovel.core.errors import ConflictError, StoryUnavailableError
from ainovel.core.field_history import FieldChangeEvent
from ainovel.core.fields import (
    ExportStoryFieldSchemaRequest,
    FieldScope,
    FieldTarget,
    FieldTemplate,
    FieldTemplateSet,
)
from ainovel.core.prompting import preview_prompt_source
from ainovel.core.records import Entry
from ainovel.core.story_fields import export_story_schema, story_schema
from ainovel.infrastructure.job_queue import JobQueue
from ainovel.mapper.discussions import DiscussionRepository
from ainovel.mapper.field_history import history as field_history
from ainovel.mapper.field_templates import FieldTemplateRepository
from ainovel.service.fields import FIELD_TEMPLATE_SCOPE_ID, read_fields
from ainovel.service.history import HistoryPage, project_history
from ainovel.service.prompt_templates import template_library
from ainovel.service.run_overview import RunOverview, build_run_overview


class ErrorBody(Model):
    detail: str


class FieldTemplateLibrary(Model):
    session_id: UUID = FIELD_TEMPLATE_SCOPE_ID
    templates: list[FieldTemplate]
    template_sets: list[FieldTemplateSet] = []


class FieldDocumentView(Model):
    revision_id: UUID
    target: FieldTarget
    entries: list[Entry]


def create_app(settings: Settings | None = None, job_queue: JobQueue | None = None) -> FastAPI:
    runtime = Runtime(settings)
    queue = job_queue or JobQueue(runtime.settings.redis_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Schema initialization is an explicit CLI action, not a request-time side effect.
        yield
        await queue.close()
        await runtime.close()

    app = FastAPI(title="AI Novel MVP", version="0.1.0", lifespan=lifespan)

    @app.get("/api/field-templates", response_model=FieldTemplateLibrary)
    async def field_templates() -> FieldTemplateLibrary:
        repository = FieldTemplateRepository(runtime.store)
        return FieldTemplateLibrary(templates=await repository.list(), template_sets=await repository.list_sets())

    @app.get("/api/stories/{story_id}/field-template-set", response_model=FieldTemplateSet)
    async def local_field_schema(story_id: UUID) -> FieldTemplateSet:
        view = await runtime.store.head(story_id)
        return story_schema(view.story)

    @app.post("/api/stories/{story_id}/field-template-set/export", response_model=FieldTemplateSet)
    async def export_field_schema(story_id: UUID, request: ExportStoryFieldSchemaRequest) -> FieldTemplateSet:
        view = await runtime.store.head(story_id)
        if request.revision_id != view.revision_id:
            raise ConflictError("小说版本已变化，请刷新后导出")
        return export_story_schema(view.story, request.name, request.description, request.targets)

    @app.get("/api/stories/{story_id}/fields", response_model=FieldDocumentView)
    async def fields(story_id: UUID, scope: FieldScope, entity_id: UUID | None = None) -> FieldDocumentView:
        view = await runtime.store.head(story_id)
        target = FieldTarget(scope=scope, entity_id=entity_id)
        return FieldDocumentView(revision_id=view.revision_id, target=target, entries=read_fields(view.story, target))

    @app.get("/api/stories/{story_id}/field-history", response_model=list[FieldChangeEvent])
    async def fields_history(story_id: UUID, revision_id: UUID | None = None, limit: int = Query(default=50, ge=1, le=200)) -> list[FieldChangeEvent]:
        view = await runtime.store.head(story_id)
        async with runtime.store.sessions() as session:
            return await field_history(session, story_id, revision_id or view.revision_id, limit)
    web = Path(__file__).resolve().parents[1] / "web"
    ui = web / "ui"
    app.mount("/assets", WebAssets(directory=web), name="assets")
    if ui.exists():
        app.mount("/ui", WebAssets(directory=ui), name="ui")

    def ui_index(legacy: str) -> FileResponse:
        return FileResponse(
            ui / "index.html" if (ui / "index.html").exists() else web / legacy,
            headers={"Cache-Control": "no-cache"},
        )

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError) -> Response:
        status = (
            410
            if isinstance(error, StoryUnavailableError)
            else 409
            if isinstance(error, ConflictError)
            else 400
        )
        return Response(
            ErrorBody(detail=str(error)).model_dump_json(), status_code=status, media_type="application/json"
        )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return ui_index("index.html")

    @app.get("/character", include_in_schema=False)
    async def character_page() -> FileResponse:
        return ui_index("character.html")

    @app.get("/characters", include_in_schema=False)
    async def character_cards_page() -> FileResponse:
        return ui_index("characters.html")

    @app.get("/discussion", include_in_schema=False)
    async def discussion_page() -> FileResponse:
        return ui_index("discussion.html")

    @app.get("/api/stories/{story_id}/discussions", response_model=list[DiscussionSession])
    async def discussion_sessions(story_id: UUID) -> list[DiscussionSession]:
        return await DiscussionRepository(runtime.store).sessions(story_id)

    @app.get("/api/stories/{story_id}/discussions/{session_id}/commands", response_model=list[CommandView])
    async def discussion_commands(story_id: UUID, session_id: UUID) -> list[CommandView]:
        return await DiscussionRepository(runtime.store).commands(story_id, session_id)

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        database, redis = await asyncio.gather(runtime.store.health(), queue.health())
        worker = bool(await queue.redis.get(queue.worker_alive)) if redis else False
        return Health(
            database=database,
            redis=redis,
            worker=worker,
            provider=runtime.settings.llm_provider,
            model=runtime.settings.llm_model,
            embedding_provider=runtime.settings.embedding_provider,
        )

    @app.post("/api/commands", response_model=CommandView, status_code=202)
    async def submit(submission: CommandSubmission) -> CommandView:
        try:
            view = await runtime.store.submit(submission)
        except IntegrityError:
            # Concurrent identical submissions are safe; conflicting payloads are rejected.
            view = await runtime.store.command(submission.id)
            if view.submission != submission:
                raise ConflictError("命令 ID 已被不同请求使用") from None
        if view.status == "queued":
            try:
                await queue.enqueue(submission.id)
            except RedisError:
                # Command is durable in PG; the outbox will deliver once Redis recovers.
                pass
        return view

    @app.get("/api/commands/{command_id}", response_model=CommandView)
    async def command(command_id: UUID) -> CommandView:
        return await runtime.store.command(command_id)

    @app.get("/api/commands/{command_id}/events", response_model=list[EventView])
    async def events(command_id: UUID, after: int = 0) -> list[EventView]:
        return await runtime.store.events(command_id, after)

    @app.get("/api/runs/{command_id}", response_model=RunOverview)
    async def run_overview(command_id: UUID) -> RunOverview:
        """Replay the persisted steps and revision changes for one user-visible run."""
        return await build_run_overview(runtime.store, command_id)

    @app.get("/api/history", response_model=HistoryPage)
    async def history(
        scope_id: UUID | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)
    ) -> HistoryPage:
        return await project_history(runtime.store, scope_id, offset, limit)

    @app.get("/api/prompt-templates", response_model=PromptTemplateLibrary)
    async def prompt_templates(story_id: UUID | None = None) -> PromptTemplateLibrary:
        if story_id is not None:
            await runtime.store.head(story_id)
        return await template_library(runtime.store, story_id)

    @app.post("/api/prompt-templates/preview", response_model=PromptTemplatePreview)
    async def prompt_template_preview(request: PromptTemplatePreviewRequest) -> PromptTemplatePreview:
        rendered = preview_prompt_source(
            request.task,
            request.source,
            request.context,
            legacy_guidance=request.guidance if request.source is None else "",
        )
        return PromptTemplatePreview(
            strategy_preview=rendered.text,
            note="这是角色与任务提示模板的完整渲染。仅包含你提供的预览上下文；运行时资料投影、工具协议与实际发送内容以本次运行步骤为准。",
            rendered_prompt=rendered.text,
            rendered_messages=[PromptPreviewMessage(role="system", content=rendered.text)],
            template_path=rendered.template_path,
            source_hash=rendered.source_hash,
            context_variables=rendered.context_variables,
            context_schema=rendered.context_schema,
        )

    @app.get("/api/commands/{command_id}/stream")
    async def stream(
        command_id: UUID, request: Request, last_event_id: str | None = Header(default=None), after: int = 0
    ) -> StreamingResponse:
        await runtime.store.command(command_id)
        try:
            cursor = max(after, int(last_event_id or "0"))
        except ValueError:
            raise HTTPException(400, "Last-Event-ID 必须为整数") from None

        async def frames() -> AsyncIterator[str]:
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                items = await runtime.store.events(command_id, cursor)
                for item in items:
                    cursor = item.id
                    yield f"id: {item.id}\ndata: {item.event.model_dump_json()}\n\n"
                view = await runtime.store.command(command_id)
                if view.status in ("completed", "failed", "waiting") and len(items) < 100:
                    # Recheck after status read to avoid missing the terminal transaction's event.
                    tail = await runtime.store.events(command_id, cursor)
                    for item in tail:
                        cursor = item.id
                        yield f"id: {item.id}\ndata: {item.event.model_dump_json()}\n\n"
                    if len(tail) < 100:
                        yield "event: done\ndata: {}\n\n"
                        return
                heartbeat += 1
                if heartbeat % 30 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/stories", response_model=list[StorySummary])
    async def stories(deleted: bool = False) -> list[StorySummary]:
        return await runtime.store.stories(deleted)

    @app.get("/api/stories/{story_id}", response_model=StoryView)
    async def story(story_id: UUID) -> StoryView:
        return await runtime.store.head(story_id)

    @app.get("/api/undo/{scope_id}", response_model=UndoState)
    async def undo_state(scope_id: UUID) -> UndoState:
        from ainovel.service.undo import UndoService

        return await UndoService(runtime.store).state(scope_id)

    @app.get("/api/sessions/{session_id}/commands", response_model=list[CommandView])
    async def session_commands(session_id: UUID) -> list[CommandView]:
        return await runtime.store.session_commands(session_id)

    @app.get("/api/drafts", response_model=list[SetupDraft])
    async def drafts(deleted: bool = False) -> list[SetupDraft]:
        from ainovel.mapper.drafts import DraftRepository

        return await DraftRepository(runtime.store).list(deleted=deleted)

    @app.delete("/api/drafts/{draft_id}", response_model=PurgeResult)
    async def purge_draft(draft_id: UUID) -> PurgeResult:
        from ainovel.service.lifecycle import purge_resource

        return await purge_resource(runtime.store, queue, draft_id, draft=True)

    @app.delete("/api/stories/{story_id}", response_model=PurgeResult)
    async def purge_story(story_id: UUID) -> PurgeResult:
        from ainovel.service.lifecycle import purge_resource

        return await purge_resource(runtime.store, queue, story_id, draft=False)

    @app.get("/api/drafts/{draft_id}", response_model=SetupDraft)
    async def draft(draft_id: UUID) -> SetupDraft:
        from ainovel.mapper.drafts import DraftRepository

        return await DraftRepository(runtime.store).get(draft_id)

    @app.get("/api/stories/{story_id}/revisions", response_model=list[Revision])
    async def revisions(story_id: UUID) -> list[Revision]:
        return await runtime.store.revisions(story_id)

    @app.get("/api/stories/{story_id}/commands", response_model=list[CommandView])
    async def commands(story_id: UUID) -> list[CommandView]:
        return await runtime.store.commands(story_id)

    @app.get("/api/revisions/{revision_id}", response_model=Revision)
    async def revision(revision_id: UUID) -> Revision:
        return await runtime.store.revision(revision_id)

    @app.get("/api/revisions/{revision_id}/characters", response_model=list[Snapshot])
    async def character_cards(revision_id: UUID, scene_id: UUID | None = None) -> list[Snapshot]:
        story = (await runtime.store.revision(revision_id)).story
        visible = {scene.id for scene in story.scenes}
        if scene_id is not None:
            try:
                limit = next(index + 1 for index, scene in enumerate(story.scenes) if scene.id == scene_id)
            except StopIteration:
                raise HTTPException(404, "场景不存在") from None
            visible = {scene.id for scene in story.scenes[:limit]}
        return [
            story.snapshot(character.id, scene_id)
            for character in story.characters
            if not character.introduced_scene_id or character.introduced_scene_id in visible
        ]

    @app.get(
        "/api/revisions/{revision_id}/characters/{character_id}/snapshots/{scene_id}", response_model=Snapshot
    )
    async def snapshot(revision_id: UUID, character_id: UUID, scene_id: UUID) -> Snapshot:
        revision = await runtime.store.revision(revision_id)
        try:
            return revision.story.snapshot(character_id, scene_id)
        except (StopIteration, ValueError):
            raise HTTPException(404, "该阶段没有此角色") from None

    return app
