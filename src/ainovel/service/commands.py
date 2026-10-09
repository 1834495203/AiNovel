"""Command handlers: orchestration and domain writes live here, never in HTTP routes."""

from abc import ABC, abstractmethod
from contextlib import aclosing
from typing import Generic, TypeVar
from uuid import UUID

from ainovel.core.characters import (
    Character,
    CharacterPatch,
    CharacterSpec,
    Experience,
    Profile,
    Snapshot,
    SnapshotContext,
)
from ainovel.core.config import Settings
from ainovel.core.discussion import DiscussionResult
from ainovel.core.domain import (
    AcceptRewriteRequest,
    ApprovalResponse,
    Chapter,
    CharacterChatRequest,
    ChatResult,
    CommandOutput,
    CommandSubmission,
    CreateStoryRequest,
    DeleteStoryRequest,
    DirectionRequest,
    Draft,
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
    StartCharacterChatRequest,
    Story,
    Trace,
    Turn,
    UndoRequest,
    UpdateStoryRequest,
    WorkflowState,
    WritingDraft,
)
from ainovel.core.errors import ConflictError
from ainovel.core.ports import KnowledgeGraph, LanguageModel
from ainovel.core.records import WorkingDraft, WorldSetting
from ainovel.llm.schemas import TextResult
from ainovel.mapper.models import CommandRow, EventRow
from ainovel.mapper.store import Store
from ainovel.service.creation import CreationProgress
from ainovel.service.prompt_templates import (
    GLOBAL_PROMPT_SCOPE_ID,
    resolve_command_template,
    resolve_run_templates,
)
from ainovel.service.retrieval import RetrievalService
from ainovel.service.workflow import NarrativeWorkflow

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
        self.workflow = NarrativeWorkflow(model, retrieval, store, settings)

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
        from ainovel.service.workflow_trace import IndexInput

        await self.store.append_event(
            Event(
                command_id=submission.id,
                type="step.started",
                trace=Trace(
                    node="index",
                    summary="建立版本检索索引并提交故事",
                    structured_input=IndexInput(
                        title=story.title, chapters=story.chapters, scenes=story.scenes
                    ).model_dump_json(),
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
    def apply_scene(state: WorkflowState) -> Story:
        from ainovel.core.domain import Scene

        if state.draft is None or state.extraction is None or state.plan is None:
            raise ValueError("生成流程未完成")
        story = state.story.model_copy(deep=True)
        chapter = (
            story.chapters[-1]
            if story.chapters and not state.new_chapter
            else Chapter(
                title=f"第{len(story.chapters) + 1}章 · {state.plan.chapter_title or state.plan.chapter_plan}"
            )
        )
        if not story.chapters or state.new_chapter:
            story.chapters.append(chapter)
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
        chapter.summary = "\n".join(scene.summary for scene in story.scenes if scene.chapter_id == chapter.id)
        story.writer = state.plan.writer_patch.apply(story.writer)
        story.writer.drafts.append(
            WorkingDraft(
                id=state.scene_id,
                name=state.draft.title,
                kind="scene",
                content=state.draft.text,
                status="used",
                source_scene_id=state.scene_id,
                reason="通过审查并提交的正文草稿",
            )
        )
        # Keep the latest working copies; older copies remain in prior immutable revisions.
        story.writer.drafts = story.writer.drafts[-12:]
        if state.maintenance:
            story.apply_metadata(state.maintenance.patch)
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
        result = await self.services.model.generate(
            InterviewResult,
            "creation_interview",
            request,
            submission.id,
            prompt_template=await resolve_command_template(
                self.services.store, submission.id, None, "creation_interview"
            ),
        )
        return CommandOutput(interview=CreationProgress().enrich(result))


class SaveSetupDraftHandler(Handler[SaveSetupDraftRequest]):
    request_type = SaveSetupDraftRequest

    async def execute(self, submission: CommandSubmission, request: SaveSetupDraftRequest) -> CommandOutput:
        from ainovel.mapper.drafts import DraftRepository

        return await DraftRepository(self.services.store).save(submission, request)


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
        return await self.services.commit(submission, story, "确认设定，创建小说")


class GenerateHandler(Handler[GenerateRequest]):
    request_type = GenerateRequest

    async def execute(self, submission: CommandSubmission, request: GenerateRequest) -> CommandOutput:
        state = await self.services.store.checkpoint(submission.id)
        if state is None:
            state = WorkflowState(
                command_id=submission.id,
                story=await self.services.source(submission),
                instruction=request.instruction,
            )
        if not state.prompt_templates:
            state.prompt_templates = await resolve_run_templates(
                self.services.store, submission.story_id, request.prompt_templates
            )
            # Pin resolved versions before the first model request so retries survive a crash.
            await self.services.store.save_checkpoint(state)
        state = await self.services.workflow.run(state)
        if state.approval_phase:
            output = CommandOutput(
                need=state.plan.new_character if state.plan else None,
                approval_phase=state.approval_phase,
                message="等待角色确认",
            )
            await self.services.store.finish(submission.id, output, "waiting")
            return output
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
            chapter_id = request.chapter_id
            source_text = "\n\n".join(scene.text for scene in target_scenes)
        else:
            scene = next(scene for scene in story.scenes if scene.id == request.scene_id)
            chapter_id = scene.chapter_id
            source_text = scene.text
        if request.end <= request.start or request.end > len(source_text):
            raise ValueError("选择范围无效")
        original = source_text[request.start : request.end]
        if original != request.selected_text:
            raise ConflictError("选中文本与源版本不同")
        directions = [
            direction.instruction
            for direction in story.directions
            if direction.active
            and (
                direction.scope in ("global", "future")
                or direction.chapter_id == chapter_id
                or direction.scope == "scene"
                and direction.scene_id == request.scene_id
            )
        ]
        result = await self.services.model.generate(
            TextResult,
            "rewrite_snippet",
            RewriteInput(
                original=original,
                scene=source_text,
                instruction=request.instruction,
                world=story.world,
                directions=directions,
            ),
            submission.id,
            prompt_template=await resolve_command_template(
                self.services.store, submission.id, submission.story_id, "rewrite_snippet"
            ),
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
                replacement=result.text,
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
        prior = story.model_copy(deep=True)
        prior.scenes = prior.scenes[:index]
        from ainovel.core.domain import Plan

        state = WorkflowState(
            command_id=submission.id,
            story=prior,
            instruction="接受局部修改",
            scene_id=scene.id,
            draft=Draft(title=scene.title, text=scene.text, summary=""),
            plan=Plan(chapter_plan="", scene_plan="", character_names=[]),
            prompt_templates=await self.services.store.pin_prompt_templates(
                submission.id, await resolve_run_templates(self.services.store, submission.story_id, [])
            ),
        )
        summary = await self.services.model.generate(
            TextResult,
            "scene_summary",
            RewriteInput(
                original=scene.text,
                scene=scene.text,
                instruction="概括实际发生的事",
                world=story.world,
                directions=[],
            ),
            submission.id,
            prompt_template=await resolve_command_template(
                self.services.store, submission.id, submission.story_id, "scene_summary"
            ),
        )
        scene.summary = summary.text
        state.draft.summary = scene.summary
        state = await self.services.workflow.extract(state)
        if state.extraction is None:
            raise ValueError("缺少状态提取")
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
            for update in state.extraction.updates
        ]
        state = await self.services.workflow.maintain(state)
        if state.maintenance:
            story.apply_metadata(state.maintenance.patch)
        for chapter in story.chapters:
            chapter.summary = "\n".join(
                item.summary for item in story.scenes if item.chapter_id == chapter.id
            )
        return await self.services.commit(
            submission,
            story,
            "接受局部修改，从此处建立分支，后续场景保留在旧版本",
            state.extraction,
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
        store = self.services.store
        async with aclosing(store.claim_lock(request.generation_command_id)) as locked:
            async for _session in locked:
                parent = await store.command(request.generation_command_id)
                if parent.submission.story_id != submission.story_id or parent.status != "waiting":
                    raise ValueError("生成命令不属于当前小说，或未等待确认")
                if parent.submission.base_revision_id != submission.base_revision_id:
                    raise ConflictError("审批的源版本不一致")
                state = await store.checkpoint(request.generation_command_id)
                if state is None or state.plan is None or state.plan.new_character is None:
                    raise ValueError("缺少待批准角色")
                if request.decision == "consent":
                    if state.approval_phase != "consent":
                        raise ValueError("该角色已进入档案确认阶段")
                    character_template = next(
                        (item for item in state.prompt_templates if item.task == "character_profile"), None
                    )
                    if character_template is None:
                        character_template = await resolve_command_template(
                            store, request.generation_command_id, submission.story_id, "character_profile"
                        )
                    character = await self.services.model.generate(
                        CharacterSpec,
                        "character_profile",
                        state,
                        submission.id,
                        prompt_template=character_template,
                    )
                    state.approval_phase = "profile"
                    output = CommandOutput(
                        need=state.plan.new_character,
                        character=character,
                        approval_phase="profile",
                        message="请检查并修改角色档案",
                    )
                    parent_status = "waiting"
                else:
                    if request.decision == "accept":
                        if state.approval_phase != "profile" or request.character is None:
                            raise ValueError("需要先同意引入角色，再确认档案")
                        if any(
                            character.name == request.character.name for character in state.story.characters
                        ):
                            raise ValueError("角色名称已存在")
                        state.approved_character = request.character
                    else:
                        state.rejected_character = True
                    state.approval_phase = None
                    state.approval_response = ApprovalResponse(
                        decision="accept" if request.decision == "accept" else "reject",
                        character=request.character,
                    )
                    output = CommandOutput(message="已确认，将恢复同一 LangGraph 执行")
                    parent_status = "queued"
                # Parent transition and approval completion share a transaction (retry-safe).
                async with store.sessions.begin() as session:
                    if submission.story_id is None:
                        raise ValueError("缺少小说")
                    await store.record_operation(
                        session, submission, related_ids=[request.generation_command_id]
                    )
                    await store.active_story(session, submission.story_id, locked=True)
                    parent_row = await session.get(
                        CommandRow, request.generation_command_id, with_for_update=True
                    )
                    resolve_row = await session.get(CommandRow, submission.id, with_for_update=True)
                    if (
                        parent_row is None
                        or resolve_row is None
                        or parent_row.status != "waiting"
                        or resolve_row.status != "running"
                    ):
                        raise ValueError("命令不存在")
                    parent_row.checkpoint = state
                    parent_row.status = parent_status
                    parent_row.output = output
                    resolve_row.status = "completed"
                    resolve_row.output = output
                    session.add(
                        EventRow(
                            command_id=submission.id,
                            payload=Event(command_id=submission.id, type="run.completed", output=output),
                        )
                    )
                    session.add(
                        EventRow(
                            command_id=request.generation_command_id,
                            payload=Event(
                                command_id=request.generation_command_id,
                                type="approval.updated",
                                output=output,
                            ),
                        )
                    )
                return output
        raise ConflictError("生成任务正在运行，请稍后审批")


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
        result = await self.services.model.generate(
            ChatResult,
            "character_chat",
            ChatInput(
                character_id=character.id,
                name=character.name,
                profile=snapshot.profile,
                snapshot=SnapshotContext.from_snapshot(snapshot),
                session_id=request.session_id,
                message=request.message,
                history=history,
                memories=self.services.retrieval.character(
                    snapshot, request.message, [character.name, *[alias.name for alias in snapshot.aliases]]
                ),
            ),
            submission.id,
            prompt_template=await resolve_command_template(
                self.services.store, submission.id, submission.story_id, "character_chat"
            ),
        )
        if result.session_id != request.session_id or result.snapshot_id != snapshot.id:
            raise ValueError("角色回复的会话或快照 ID 不符")
        return CommandOutput(chat=result, message="角色采访已保存，不改变正文")


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
        from ainovel.service.recovery import RecoverCommandHandler

        self.services = services
        self.handlers = (
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
