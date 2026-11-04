"""Command handlers: orchestration and domain writes live here, never in HTTP routes."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from ainovel.core.characters import (
    Character,
    CharacterPatch,
    Experience,
    Profile,
    Snapshot,
    SnapshotContext,
)
from ainovel.core.config import Settings
from ainovel.core.discussion import DiscussionResult
from ainovel.core.domain import (
    AcceptRewriteRequest,
    Chapter,
    CharacterChatRequest,
    ChatResult,
    CommandOutput,
    CommandSubmission,
    CreateStoryRequest,
    DeleteStoryRequest,
    DirectionRequest,
    Event,
    Extraction,
    GenerateRequest,
    InterviewRequest,
    InterviewResult,
    Model,
    ReindexRequest,
    RejectRewriteRequest,
    ResolveCharacterRequest,
    RestoreStoryRequest,
    RevertRequest,
    RewriteProposal,
    RewriteRequest,
    SavePromptTemplateRequest,
    SaveSetupDraftRequest,
    SetSetupDraftDeletedRequest,
    StartCharacterChatRequest,
    Story,
    Trace,
    Turn,
    UndoRequest,
    UpdateStoryRequest,
    WritingDraft,
    WritingState,
)
from ainovel.core.errors import ConflictError
from ainovel.core.ports import KnowledgeGraph, LanguageModel
from ainovel.core.records import WorldSetting
from ainovel.mapper.models import CommandRow, EventRow
from ainovel.mapper.store import Store
from ainovel.service.creation import CreationProgress
from ainovel.service.prompt_templates import (
    GLOBAL_PROMPT_SCOPE_ID,
    resolve_command_template,
    resolve_run_templates,
)
from ainovel.service.retrieval import RetrievalService
from ainovel.service.writing_agent import WritingAgent

R = TypeVar("R", bound=Model)


class ChatInput(Model):
    character_id: UUID
    name: str
    profile: Profile
    snapshot: SnapshotContext
    session_id: UUID
    memories: list[Experience]
    history: list[Turn]
    message: str


class RewriteInput(Model):
    original: str
    scene: str
    instruction: str
    world: WorldSetting
    directions: list[str]


class Services:
    def __init__(
        self,
        store: Store,
        model: LanguageModel,
        retrieval: RetrievalService,
        graph: KnowledgeGraph,
        settings: Settings,
    ) -> None:
        self.store = store
        self.model = model
        self.retrieval = retrieval
        self.graph = graph
        self.settings = settings
        self.writing_agent = WritingAgent(model, retrieval, store, settings)

    async def source(self, submission: CommandSubmission) -> Story:
        if submission.base_revision_id is None:
            raise ValueError("缺少源版本")
        revision = await self.store.revision(submission.base_revision_id)
        if revision.story_id != submission.story_id:
            raise ValueError("源版本不属于小说")
        return revision.story.model_copy(deep=True)

    async def commit(
        self,
        submission: CommandSubmission,
        story: Story,
        reason: str,
        extraction: Extraction | None = None,
        consumed_proposal_id: UUID | None = None,
        confirmed_generation_id: UUID | None = None,
        writing_draft: WritingDraft | None = None,
        discussion: "DiscussionResult | None" = None,
    ) -> CommandOutput:


        story = Story.model_validate(story.model_dump(round_trip=True))

        await self.store.append_event(
            Event(
                command_id=submission.id,
                type="step.started",
                trace=Trace(
                    node="index",
                    summary="建立版本检索索引并提交故事",
                    structured_input=story.model_dump_json(round_trip=True,include={"title","chapters","scenes"}),
                ),
            )
        )
        chunks, embeddings = await self.retrieval.index(story, submission.id)
        await self.graph.prepare(story, extraction)
        return await self.store.commit(
            submission,
            story,
            reason,
            chunks,
            embeddings,
            consumed_proposal_id,
            confirmed_generation_id,
            writing_draft,
            discussion,
        )

    @staticmethod
    def apply_scene(state: WritingState) -> Story:
        from ainovel.core.domain import Scene

        if state.draft is None or state.extraction is None or state.plan is None:
            raise ValueError("生成流程未完成")
        story = state.story.model_copy(deep=True)
        from ainovel.core.fields import FieldTarget
        from ainovel.core.story_fields import ensure_story_schema, initial_fields

        ensure_story_schema(story)
        chapter = (
            story.chapters[-1]
            if story.chapters and not state.new_chapter
            else Chapter(
                title=f"第{len(story.chapters) + 1}章 · {state.plan.chapter_title or state.plan.chapter_plan}"
            )
        )
        if not story.chapters or state.new_chapter:
            story.chapters.append(chapter)
            chapter.custom_fields = initial_fields(
                story, FieldTarget(scope="chapter", entity_id=chapter.id), chapter.custom_fields
            )
        patches = [
            CharacterPatch(
                character_id=update.character_id,
                scene_id=state.scene_id,
                state=update.state,
                profile=update.profile,
                aliases=update.aliases,
                custom_fields=update.custom_fields,
                new_experiences=update.new_experiences,
                reason=update.reason,
            )
            for update in state.extraction.updates
        ]
        story.scenes.append(
            Scene(
                id=state.scene_id,
                chapter_id=chapter.id,
                title=state.draft.title,
                text=state.draft.text,
                summary=state.draft.summary,
                character_patches=patches,
                source_command_id=state.execution_id,
            )
        )
        story.scenes[-1].custom_fields = initial_fields(
            story, FieldTarget(scope="scene", entity_id=state.scene_id), story.scenes[-1].custom_fields
        )
        chapter.summary = "\n".join(scene.summary for scene in story.scenes if scene.chapter_id == chapter.id)
        story.writer = state.plan.writer_patch.apply(story.writer)
        for change in state.proposed_material_changes:
            from ainovel.core.records import merge_records
            from ainovel.core.story_fields import read_fields
            from ainovel.service.fields import write_fields

            write_fields(
                story,
                change.target,
                merge_records(read_fields(story, change.target), change.patch),
                change.reason,
            )
        for direction in story.directions:
            if direction.scope == "scene" and direction.scene_id is None and direction.active:
                direction.scene_id = state.scene_id
                direction.active = False
        return story


class Handler(ABC, Generic[R]):
    request_type: type[R]

    def __init__(self, services: Services) -> None:
        self.services = services

    def supports(self, request: Model) -> bool:
        return isinstance(request, self.request_type)

    async def handle(self, submission: CommandSubmission) -> CommandOutput:
        request = self.request_type.model_validate(submission.payload)
        return await self.execute(submission, request)

    @abstractmethod
    async def execute(self, submission: CommandSubmission, request: R) -> CommandOutput: ...


class InterviewHandler(Handler[InterviewRequest]):
    request_type = InterviewRequest

    async def execute(self, submission: CommandSubmission, request: InterviewRequest) -> CommandOutput:
        from ainovel.core.agent import SubmitCreationAction
        from ainovel.core.discussion import DiscussionTurn
        from ainovel.service.agent_loop import context_for, run_agent

        template = await resolve_command_template(
            self.services.store, submission.id, None, "creation_interview"
        )
        history = [DiscussionTurn(role=turn.role, content=turn.content) for turn in request.turns[:-1]]
        goal = request.turns[-1].content if request.turns else "请开始了解我的小说构思"
        story = Story(title="新小说")

        def build(results):
            return context_for(
                story, submission.id, "creation_interview", goal, history=history, results=results
            )

        async def execute(action):
            raise ValueError("创建访谈只能通过提案工具提交初始资料")

        result = await run_agent(self.services, submission.id, "creation_interview", template, build, execute)
        interview = (
            result.arguments
            if isinstance(result, SubmitCreationAction)
            else InterviewResult(reply=result.reply)
        )
        return CommandOutput(interview=CreationProgress().enrich(interview))


class SaveSetupDraftHandler(Handler[SaveSetupDraftRequest]):
    request_type = SaveSetupDraftRequest

    async def execute(self, submission: CommandSubmission, request: SaveSetupDraftRequest) -> CommandOutput:
        from ainovel.mapper.drafts import DraftRepository

        return await DraftRepository(self.services.store).save(submission, request)


class SetSetupDraftDeletedHandler(Handler[SetSetupDraftDeletedRequest]):
    request_type = SetSetupDraftDeletedRequest

    async def execute(
        self, submission: CommandSubmission, request: SetSetupDraftDeletedRequest
    ) -> CommandOutput:
        from ainovel.mapper.drafts import DraftRepository

        return await DraftRepository(self.services.store).set_deleted(submission, request.deleted)


class CreateStoryHandler(Handler[CreateStoryRequest]):
    request_type = CreateStoryRequest

    async def execute(self, submission: CommandSubmission, request: CreateStoryRequest) -> CommandOutput:
        story = Story(
            title=request.proposal.title,
            world=request.proposal.world,
            preferences=request.proposal.preferences,
            custom_fields=request.proposal.custom_fields,
            characters=[Character.model_validate(spec.model_dump()) for spec in request.proposal.characters],
        )
        if not (story.world.premise.strip() or story.world.entries):
            raise ValueError("请先定义基本世界观方向")
        story.initial_snapshots = [
            Snapshot(
                character_id=character.id,
                profile=character.profile,
                aliases=character.aliases,
                custom_fields=character.custom_fields,
            )
            for character in story.characters
        ]
        from ainovel.core.field_templates import default_template_set
        from ainovel.mapper.field_templates import FieldTemplateRepository
        from ainovel.service.fields import initialize_story_schema

        repository = FieldTemplateRepository(self.services.store)
        if request.field_template_set_id and request.field_template_ids:
            raise ValueError("请选择小说模板套装，不能同时指定旧版分类模板")
        template_set = await repository.get_set(request.field_template_set_id or default_template_set().id)
        if request.field_template_ids:
            selected = [await repository.get(identity) for identity in request.field_template_ids]
            scopes = [item.scope for item in selected]
            if len(scopes) != len(set(scopes)):
                raise ValueError("每类资料只能选择一个初始化模板")
            template_set.templates = [
                item for item in template_set.templates if item.scope not in scopes
            ] + selected
        initialize_story_schema(story, template_set)
        return await self.services.commit(submission, story, "确认设定，创建小说")


class GenerateHandler(Handler[GenerateRequest]):
    request_type = GenerateRequest

    async def execute(self, submission: CommandSubmission, request: GenerateRequest) -> CommandOutput:
        state = await self.services.store.checkpoint(submission.id)
        if state is None:
            from ainovel.core.domain import Intent
            from ainovel.service.scene_planning import next_scene

            story = await self.services.source(submission)
            planned = next_scene(story)
            if planned is None or request.planned_scene_id != planned.id:
                raise ValueError("请先在设定模式创建幕，并选择当前路线的下一幕")
            state = WritingState(
                command_id=submission.id,
                source_revision_id=submission.base_revision_id,
                story=story,
                instruction=request.instruction + "\n严格依据已保存的幕计划生成正文，不改写幕设定。",
                planned_scene_id=planned.id,
                scene_id=planned.id,
                plan=planned.plan.model_copy(deep=True),
                intent=Intent(goal=request.instruction, route="deliberate"),
                new_chapter=not story.chapters or planned.plan.start_new_chapter,
                text_only_revision=True,
            )
        if not state.prompt_templates:
            state.prompt_templates = await resolve_run_templates(
                self.services.store, submission.story_id, request.prompt_templates
            )
            # Pin resolved versions before the first model request so retries survive a crash.
            await self.services.store.save_checkpoint(state)
        state = await self.services.writing_agent.run(state)
        if state.draft_status == "pending" and state.draft is not None:
            output = CommandOutput(
                story_id=submission.story_id,
                writing_draft=WritingDraft(
                    generation_command_id=submission.id, version=state.draft_version, content=state.draft
                ),
                message="草稿已保存，请修改或确认；正式故事尚未更新",
            )
            await self.services.store.finish(submission.id, output, "waiting")
            return output
        raise ValueError("生成流程未到达草稿确认点")


class DirectionHandler(Handler[DirectionRequest]):
    request_type = DirectionRequest

    async def execute(self, submission: CommandSubmission, request: DirectionRequest) -> CommandOutput:
        story = await self.services.source(submission)
        direction = request.direction
        if direction.chapter_id and not any(chapter.id == direction.chapter_id for chapter in story.chapters):
            raise ValueError("创作备注的章节不存在")
        if direction.scene_id and not any(scene.id == direction.scene_id for scene in story.scenes):
            raise ValueError("创作备注的场景不存在")
        story.directions = [existing for existing in story.directions if existing.id != direction.id]
        story.directions.append(direction)
        return await self.services.commit(submission, story, "更新创作备注（不触发写作）")


class RewriteHandler(Handler[RewriteRequest]):
    request_type = RewriteRequest

    async def execute(self, submission: CommandSubmission, request: RewriteRequest) -> CommandOutput:
        story = await self.services.source(submission)
        if request.chapter_id is not None:
            target_scenes = [scene for scene in story.scenes if scene.chapter_id == request.chapter_id]
            if not target_scenes:
                raise ValueError("章节不存在或为空")
            source_text = "\n\n".join(scene.text for scene in target_scenes)
        else:
            scene = next(scene for scene in story.scenes if scene.id == request.scene_id)
            source_text = scene.text
        if request.end <= request.start or request.end > len(source_text):
            raise ValueError("选择范围无效")
        original = source_text[request.start : request.end]
        if original != request.selected_text:
            raise ConflictError("选中文本与源版本不同")
        result = await self.services.writing_agent.propose_rewrite(
            self.services,
            submission,
            story,
            source_text,
            original,
            request.instruction,
            request.start,
            request.end,
        )
        if submission.base_revision_id is None:
            raise ValueError("缺少源版本")
        return CommandOutput(
            rewrite=RewriteProposal(
                source_revision_id=submission.base_revision_id,
                scene_id=request.scene_id,
                chapter_id=request.chapter_id,
                start=request.start,
                end=request.end,
                original=original,
                replacement=result.draft.text,
                summary=result.draft.summary,
                updates=result.updates,
                materials=result.materials,
            )
        )


class AcceptRewriteHandler(Handler[AcceptRewriteRequest]):
    request_type = AcceptRewriteRequest

    async def execute(self, submission: CommandSubmission, request: AcceptRewriteRequest) -> CommandOutput:
        proposed = await self.services.store.command(request.proposal_command_id)
        proposal = proposed.output.rewrite
        if (
            proposed.status != "completed"
            or proposed.submission.story_id != submission.story_id
            or proposal is None
            or proposal.source_revision_id != submission.base_revision_id
            or proposal.status != "proposed"
        ):
            raise ConflictError("修改建议不属于当前版本或小说")
        story = await self.services.source(submission)
        index = next(
            i
            for i, scene in enumerate(story.scenes)
            if (
                scene.id == proposal.scene_id
                if proposal.scene_id
                else scene.chapter_id == proposal.chapter_id
            )
        )
        scene = story.scenes[index]
        source_text = (
            scene.text
            if proposal.scene_id
            else "\n\n".join(item.text for item in story.scenes if item.chapter_id == proposal.chapter_id)
        )
        if source_text[proposal.start : proposal.end] != proposal.original:
            raise ConflictError("源选区已变化")
        # A historical rewrite forks at this scene. Old suffix is retained in its immutable revision.
        story.scenes = story.scenes[: index + 1]
        scene.text = source_text[: proposal.start] + proposal.replacement + source_text[proposal.end :]
        scene.source_command_id = submission.id
        active_chapters = {item.chapter_id for item in story.scenes}
        story.chapters = [chapter for chapter in story.chapters if chapter.id in active_chapters]
        scene.character_patches = []  # Re-extract against the state before the rewritten scene.
        kept_scenes = {item.id for item in story.scenes}
        story.characters = [
            character
            for character in story.characters
            if character.introduced_scene_id is None or character.introduced_scene_id in kept_scenes
        ]
        kept_characters = {character.id for character in story.characters}
        story.initial_snapshots = [
            snapshot for snapshot in story.initial_snapshots if snapshot.character_id in kept_characters
        ]
        story.directions = [
            direction
            for direction in story.directions
            if (direction.chapter_id is None or direction.chapter_id in active_chapters)
            and (direction.scene_id is None or direction.scene_id in kept_scenes)
        ]
        scene.summary = proposal.summary
        extraction = Extraction(updates=proposal.updates)
        scene.character_patches = [
            CharacterPatch(
                character_id=update.character_id,
                scene_id=scene.id,
                state=update.state,
                profile=update.profile,
                aliases=update.aliases,
                custom_fields=update.custom_fields,
                new_experiences=update.new_experiences,
                reason=update.reason,
            )
            for update in extraction.updates
        ]
        from ainovel.core.records import merge_records
        from ainovel.core.story_fields import read_fields
        from ainovel.service.fields import write_fields

        for change in proposal.materials:
            write_fields(
                story,
                change.target,
                merge_records(read_fields(story, change.target), change.patch),
                change.reason,
            )
        for chapter in story.chapters:
            chapter.summary = "\n".join(
                item.summary for item in story.scenes if item.chapter_id == chapter.id
            )
        return await self.services.commit(
            submission,
            story,
            "接受局部修改，从此处建立分支，后续场景保留在旧版本",
            extraction,
            proposed.submission.id,
        )


class RejectRewriteHandler(Handler[RejectRewriteRequest]):
    request_type = RejectRewriteRequest

    async def execute(self, submission: CommandSubmission, request: RejectRewriteRequest) -> CommandOutput:
        async with self.services.store.sessions.begin() as session:
            await self.services.store.record_operation(
                session, submission, related_ids=[request.proposal_command_id]
            )
            proposed = await session.get(CommandRow, request.proposal_command_id, with_for_update=True)
            if (
                proposed is None
                or proposed.story_id != submission.story_id
                or proposed.output.rewrite is None
                or proposed.output.rewrite.source_revision_id != submission.base_revision_id
            ):
                raise ValueError("修改建议不属于当前小说与源版本")
            if proposed.output.rewrite.status == "accepted":
                raise ConflictError("该建议已经接受，请使用版本回退")
            output = proposed.output.model_copy(deep=True)
            output.rewrite.status = "rejected"
            proposed.output = output
            result = CommandOutput(message="已放弃修改建议，正文保持原版本")
            command = await session.get(CommandRow, submission.id, with_for_update=True)
            if command is None or command.status != "running":
                raise ConflictError("任务已停止")
            command.status = "completed"
            command.output = result
            session.add(
                EventRow(
                    command_id=submission.id,
                    payload=Event(command_id=submission.id, type="run.completed", output=result),
                )
            )
        return result


class ResolveCharacterHandler(Handler[ResolveCharacterRequest]):
    request_type = ResolveCharacterRequest

    async def execute(self, submission: CommandSubmission, request: ResolveCharacterRequest) -> CommandOutput:
        raise ValueError("人物不再由正文流水线自动审批；请在幕设定阶段完成角色资料后再写正文")


class RevertHandler(Handler[RevertRequest]):
    request_type = RevertRequest

    async def execute(self, submission: CommandSubmission, request: RevertRequest) -> CommandOutput:
        target = await self.services.store.revision(request.target_revision_id)
        if target.story_id != submission.story_id:
            raise ValueError("目标版本不属于当前小说")
        return await self.services.commit(
            submission, target.story.model_copy(deep=True), "回退为所选历史版本"
        )


class CharacterChatHandler(Handler[CharacterChatRequest]):
    request_type = CharacterChatRequest

    async def execute(self, submission: CommandSubmission, request: CharacterChatRequest) -> CommandOutput:
        story = await self.services.source(submission)
        character = next(character for character in story.characters if character.id == request.character_id)
        snapshot = story.snapshot(character.id, request.scene_id)
        previous = await self.services.store.chat_history(submission)
        history: list[Turn] = []
        for command in previous[-20:]:
            payload = command.submission.payload
            if isinstance(payload, CharacterChatRequest) and command.output.chat:
                history.extend(
                    [
                        Turn(role="user", content=payload.message),
                        Turn(role="assistant", content=command.output.chat.reply),
                    ]
                )
        from ainovel.core.agent import SubmitCreationAction, SubmitDraftAction
        from ainovel.core.discussion import DiscussionTurn
        from ainovel.service.agent_loop import context_for, read_tool, run_agent

        stage = story.model_copy(deep=True)
        index = next(i for i, item in enumerate(stage.scenes) if item.id == request.scene_id)
        stage.scenes = stage.scenes[: index + 1]
        stage.planned_scenes = []
        stage.active_scene_leaf_id = None
        stage.world = WorldSetting()
        stage.writer = type(stage.writer)()
        stage.preferences = type(stage.preferences)()
        stage.characters = [item for item in stage.characters if item.id == character.id]
        template = await resolve_command_template(
            self.services.store, submission.id, submission.story_id, "character_chat"
        )

        def build(results):
            return context_for(
                stage,
                submission.base_revision_id,
                "character_chat",
                request.message,
                history=[DiscussionTurn(role=t.role, content=t.content) for t in history],
                results=results,
                task_input={"character_name": character.name, "stage_scene_id": str(request.scene_id),"known_experiences":[item.model_dump(mode="json") for item in snapshot.experiences[-5:]]},
                recent=0,
            )

        async def execute(action):
            if (
                action.arguments.scope not in ("character", "state")
                or action.arguments.entity_id != character.id
            ):
                raise ValueError("角色对话只能读取本角色在选定阶段的资料")
            return await read_tool(self.services, stage, submission.base_revision_id, action, submission.id)

        result = await run_agent(self.services, submission.id, "character_chat", template, build, execute)
        if isinstance(result, (SubmitDraftAction, SubmitCreationAction)):
            raise ValueError("角色对话不能提交小说修改")
        return CommandOutput(
            chat=ChatResult(session_id=request.session_id, snapshot_id=snapshot.id, reply=result.reply)
        )


class StartCharacterChatHandler(Handler[StartCharacterChatRequest]):
    request_type = StartCharacterChatRequest

    async def execute(
        self, submission: CommandSubmission, request: StartCharacterChatRequest
    ) -> CommandOutput:
        story = await self.services.source(submission)
        story.snapshot(request.character_id, request.scene_id)
        return CommandOutput(chat_session_id=request.session_id, message="已创建角色会话")


class UpdateStoryHandler(Handler[UpdateStoryRequest]):
    request_type = UpdateStoryRequest

    async def execute(self, submission: CommandSubmission, request: UpdateStoryRequest) -> CommandOutput:
        story = await self.services.source(submission)
        story.apply_metadata(request.patch)
        for edit in request.character_edits:
            current = story.snapshot(edit.character_id)
            if story.scenes:
                story.scenes[-1].character_patches.append(
                    CharacterPatch(
                        character_id=edit.character_id,
                        scene_id=story.scenes[-1].id,
                        profile=edit.profile,
                        aliases=edit.aliases,
                        custom_fields=edit.custom_fields,
                        reason=edit.reason,
                    )
                )
            else:
                baseline = next(
                    item for item in story.initial_snapshots if item.character_id == edit.character_id
                )
                baseline.profile = edit.profile.apply(current.profile)
                from ainovel.core.records import merge_records

                baseline.aliases = merge_records(current.aliases, edit.aliases)
                baseline.custom_fields = merge_records(current.custom_fields, edit.custom_fields)
        for patch in request.character_patches:
            if not story.scenes or patch.scene_id != story.scenes[-1].id:
                raise ValueError("角色资料修改需要定位当前最新场景，历史修改请先回退版本")
            story.snapshot(patch.character_id).apply(patch)  # Validate the target before commit.
            story.scenes[-1].character_patches.append(patch)
        return await self.services.commit(
            submission, story, request.patch.reason or "更新世界观、偏好或作家记录"
        )


class ReindexHandler(Handler[ReindexRequest]):
    request_type = ReindexRequest

    async def execute(self, submission: CommandSubmission, request: ReindexRequest) -> CommandOutput:
        return await self.services.commit(
            submission, await self.services.source(submission), "重建当前版本检索索引"
        )


class DeleteStoryHandler(Handler[DeleteStoryRequest]):
    request_type = DeleteStoryRequest

    async def execute(self, submission: CommandSubmission, request: DeleteStoryRequest) -> CommandOutput:
        return await self.services.store.set_deleted(submission, True)


class RestoreStoryHandler(Handler[RestoreStoryRequest]):
    request_type = RestoreStoryRequest

    async def execute(self, submission: CommandSubmission, request: RestoreStoryRequest) -> CommandOutput:
        return await self.services.store.set_deleted(submission, False)


class UndoHandler(Handler[UndoRequest]):
    request_type = UndoRequest

    async def execute(self, submission: CommandSubmission, request: UndoRequest) -> CommandOutput:
        from ainovel.service.undo import UndoService

        return await UndoService(self.services.store).execute(submission, request)


class SavePromptTemplateHandler(Handler[SavePromptTemplateRequest]):
    request_type = SavePromptTemplateRequest

    async def execute(
        self, submission: CommandSubmission, request: SavePromptTemplateRequest
    ) -> CommandOutput:
        if request.scope == "global":
            if submission.session_id != GLOBAL_PROMPT_SCOPE_ID:
                raise ValueError("全局模板需要固定的模板设置撤回范围")
            scope_id = GLOBAL_PROMPT_SCOPE_ID
        else:
            if submission.story_id is None:
                raise ValueError("作品模板缺少小说")
            scope_id = submission.story_id
        version = await self.services.store.save_prompt_template(submission, request, scope_id)
        message = "已恢复默认模板" if version is None else f"已保存并启用 {version.name} v{version.version}"
        return CommandOutput(prompt_template=version, message=message)


class CommandBus:
    def __init__(self, services: Services) -> None:
        from ainovel.service.discussion import DiscussionHandler, StartDiscussionHandler
        from ainovel.service.draft_review import ReviewDraftHandler
        from ainovel.service.fields import (
            ApplyFieldTemplateHandler,
            EditFieldsHandler,
            InitializeStoryTemplateSetHandler,
            SaveFieldTemplateHandler,
            SaveFieldTemplateSetHandler,
            UpdateStoryFieldSchemaHandler,
        )
        from ainovel.service.recovery import RecoverCommandHandler
        from ainovel.service.scene_planning import PlanSceneHandler, SelectSceneBranchHandler

        self.services = services
        self.handlers = (
            PlanSceneHandler(services),
            SelectSceneBranchHandler(services),
            ApplyFieldTemplateHandler(services),
            EditFieldsHandler(services),
            SaveFieldTemplateHandler(services),
            SaveFieldTemplateSetHandler(services),
            InitializeStoryTemplateSetHandler(services),
            UpdateStoryFieldSchemaHandler(services),
            DiscussionHandler(services),
            StartDiscussionHandler(services),
            RecoverCommandHandler(services),
            ReviewDraftHandler(services),
            UpdateStoryHandler(services),
            InterviewHandler(services),
            CreateStoryHandler(services),
            GenerateHandler(services),
            DirectionHandler(services),
            RewriteHandler(services),
            AcceptRewriteHandler(services),
            RejectRewriteHandler(services),
            ResolveCharacterHandler(services),
            RevertHandler(services),
            CharacterChatHandler(services),
            StartCharacterChatHandler(services),
            SaveSetupDraftHandler(services),
            SetSetupDraftDeletedHandler(services),
            SavePromptTemplateHandler(services),
            ReindexHandler(services),
            DeleteStoryHandler(services),
            RestoreStoryHandler(services),
            UndoHandler(services),
        )

    async def execute(self, submission: CommandSubmission) -> CommandOutput:
        for handler in self.handlers:
            if handler.supports(submission.payload):
                return await handler.handle(submission)
        raise ValueError("没有对应的命令处理器")
