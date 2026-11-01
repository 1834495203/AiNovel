"""LangGraph orchestration: proposals never become canonical facts before final-text extraction."""

from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ainovel.core.characters import Character, Experience, Profile, Snapshot, SnapshotContext
from ainovel.core.config import Settings
from ainovel.core.domain import (
    ApprovalResponse,
    Consultation,
    Draft,
    DraftReviewResponse,
    Event,
    Intent,
    JudgeResult,
    Model,
    NarrativeContext,
    Plan,
    RevisionPlan,
    StoryMaintenance,
    Trace,
    WorkflowState,
)
from ainovel.core.errors import DraftRevisionError
from ainovel.core.ports import LanguageModel
from ainovel.core.text_edits import TextEditConflict, apply_text_edits
from ainovel.infrastructure.checkpoints import GraphCheckpoints
from ainovel.llm.schemas import (
    DraftRevisionResult,
    FinalizationResult,
    PlanningDecision,
    RewriteSelectionResult,
    WritingInput,
    maintenance_due,
    project_context,
)
from ainovel.mapper.store import Store
from ainovel.service.retrieval import RetrievalService
from ainovel.service.subgraphs import (
    characters_graph,
    finalization_graph,
    planning_graph,
    recall_graph,
    writing_graph,
)


class DraftRewriteInput(Model):
    original_context: WritingInput
    scene: str
    selection: str
    start: int
    end: int
    instruction: str


class ConsultationInput(Model):
    character_id: UUID
    name: str
    profile: Profile
    snapshot: SnapshotContext
    situation: str
    memories: list[Experience]
    instruction: str


class NarrativeWorkflow:
    def __init__(
        self, model: LanguageModel, retrieval: RetrievalService, store: Store, settings: Settings
    ) -> None:
        self.model = model
        self.retrieval = retrieval
        self.store = store
        self.settings = settings
        self.checkpoints = GraphCheckpoints(settings)
        graph = StateGraph(WorkflowState)
        graph.add_node("planning", planning_graph(self))
        graph.add_node("recall", recall_graph(self))
        graph.add_node("characters", characters_graph(self))
        graph.add_node("writing", writing_graph(self))
        graph.add_node("review", self.review)
        graph.add_node("finalize", finalization_graph(self))
        graph.add_edge(START, "planning")
        graph.add_edge("planning", "recall")
        graph.add_edge("recall", "characters")
        graph.add_edge("characters", "writing")
        graph.add_edge("writing", "review")
        graph.add_conditional_edges("review", self.after_review)
        graph.add_edge("finalize", END)
        self.builder = graph
        self.graph = graph.compile()
        self.durable_graph = False

    async def step(self, state: WorkflowState, node: str, summary: str) -> None:
        from ainovel.service.workflow_trace import node_input, node_output

        await self.store.append_event(
            Event(
                command_id=state.execution_id,
                actor="ai",
                type="step.started",
                trace=Trace(
                    node=node,
                    summary=summary,
                    structured_input=node_input(state, node).model_dump_json(exclude_none=True),
                    structured_output=node_output(state, node).model_dump_json(exclude_none=True)
                    if node in ("draft_review", "approval")
                    else "",
                ),
            )
        )

    async def saved(self, state: WorkflowState, node: str) -> WorkflowState:
        from ainovel.service.workflow_trace import node_output

        await self.store.save_checkpoint(state)
        await self.store.append_event(
            Event(
                command_id=state.execution_id,
                actor="ai",
                type="step.completed",
                trace=Trace(
                    node=node,
                    summary="完成",
                    structured_output=node_output(state, node).model_dump_json(exclude_none=True),
                ),
            )
        )
        return state

    async def analyze(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "analyze", "理解创作要求")
        if state.feedback:
            return await self.saved(state, "analyze")
        if state.intent is None:
            decision = await self.model.generate(
                PlanningDecision,
                "planning_decision",
                state,
                state.execution_id,
            )
            state.intent = decision.intent
            state.plan = decision.plan
            if (
                not state.story.writer.mainlines
                and not state.story.writer.outline
                and (
                    not state.plan.writer_patch.mainlines.upsert or not state.plan.writer_patch.outline.upsert
                )
            ):
                state.plan = await self.model.generate(
                    Plan,
                    "planning",
                    state,
                    state.execution_id,
                )
                if not state.plan.writer_patch.mainlines.upsert or not state.plan.writer_patch.outline.upsert:
                    raise ValueError("首次规划未建立主线和大纲，请重试深入规划")
            if not state.story.scenes:
                state.intent.route = "deliberate"
                if not state.plan.start_new_chapter:
                    state.plan.start_new_chapter = True
            if (
                state.intent.route == "fast"
                and not state.story.writer.mainlines
                and not state.story.writer.outline
            ):
                state.intent.route = "deliberate"
        return await self.saved(state, "analyze")

    async def prepare_context(self, state: WorkflowState) -> WorkflowState:
        if state.feedback:
            # A draft revision starts with the exact context that produced this draft.
            return state
        chapter_id = state.story.chapters[-1].id if state.story.chapters and not state.new_chapter else None
        directions = [
            direction
            for direction in state.story.directions
            if direction.active
            and (
                direction.scope in ("future", "global")
                or (direction.scope == "chapter" and direction.chapter_id == chapter_id)
                or (direction.scope == "scene" and direction.scene_id is None)
            )
        ]
        state.context = NarrativeContext(
            recent_scenes=state.story.scenes[-self.settings.recent_scenes :],
            directions=directions,
        )
        return state

    def recall_route(self, state: WorkflowState) -> str:
        return "retrieve" if state.intent and state.intent.use_retrieval else "skip_retrieval"

    async def skip_retrieval(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "skip_retrieval", "跳过远期召回，保留近期正文与创作备注")
        return await self.saved(state, "skip_retrieval")

    async def retrieve(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "retrieve", "召回章节、段落与近期故事")
        command = await self.store.command(state.command_id)
        revision_id = command.submission.base_revision_id
        if revision_id is None:
            raise ValueError("缺少源版本")
        recalled = await self.retrieval.narrative(
            revision_id, state.feedback.feedback if state.feedback else state.instruction, state.execution_id
        )
        if state.feedback:
            known = {chunk.id for chunk in state.context.recalled}
            state.context.recalled = sorted(
                state.context.recalled + [chunk for chunk in recalled if chunk.id not in known],
                key=lambda chunk: (chunk.ordinal, chunk.kind),
            )
        else:
            state.context.recalled = recalled
        return await self.saved(state, "retrieve")

    def planning_route(self, state: WorkflowState) -> str:
        if state.feedback:
            return "rewrite_plan"
        return "quick_plan" if state.intent and state.intent.route == "fast" else "plan"

    async def rewrite_plan(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "rewrite_plan", "分析草稿修改范围与重写策略")
        if state.text_only_revision or (state.feedback and state.feedback.text_only):
            state.revision_plan = RevisionPlan(
                goal="只重新生成正文，保留本幕原有设定和规划",
                instructions=["只修改正文表达，不改变原有设定、规划或引入新的持续角色。"],
                character_names=state.plan.character_names if state.plan else [],
            )
        else:
            state.revision_plan = await self.model.generate(
                RevisionPlan, "revision_plan", state, state.execution_id,
            )
        revision = state.revision_plan
        previous = state.intent or Intent(goal=state.instruction)
        state.intent = previous.model_copy(
            update={
                "route": "deliberate" if revision.review_required else "fast",
                "route_reason": revision.decision_reason or "按修改范围选择必要步骤，默认复用原上下文",
                "use_retrieval": revision.use_retrieval,
                "use_characters": revision.use_characters,
                "retrieval_reason": "按修改意见补充相关前文"
                if revision.use_retrieval
                else "复用本幕已有召回",
                "characters_reason": "修改涉及人物动机或行为，重新访谈"
                if revision.use_characters
                else "复用原角色访谈",
            }
        )
        if state.feedback and state.feedback.selection:
            constraint = "只修改选区，选区外正文完全不变"
            if constraint not in state.intent.constraints:
                state.intent.constraints.append(constraint)
        if revision.replan:
            state.plan = await self.model.generate(Plan, "planning", state, state.execution_id)
            state.new_chapter = not state.story.chapters or state.plan.start_new_chapter
        if revision.use_characters:
            state.consultations = []
        if state.plan is not None:
            if revision.character_names and (
                revision.replan or revision.use_characters or revision.new_character
            ):
                state.plan.character_names = state.revision_plan.character_names
            if state.revision_plan.new_character:
                state.plan.new_character = state.revision_plan.new_character
                state.approved_character = None
                state.rejected_character = False
            elif revision.replan and state.plan.new_character:
                if (
                    state.approved_character is None
                    or state.approved_character.name != state.plan.new_character.name
                ):
                    state.approved_character = None
                    state.rejected_character = False
        return await self.saved(state, "rewrite_plan")

    async def quick_plan(self, state: WorkflowState) -> WorkflowState:
        return await self.plan(state, quick=True)

    async def plan(self, state: WorkflowState, quick: bool = False) -> WorkflowState:
        await self.step(
            state,
            "quick_plan" if quick else "plan",
            "复用长期计划，快速规划下一幕" if quick else "规划整部故事主线、支线、伏笔与当前场景",
        )
        if state.plan is None:
            state.plan = await self.model.generate(
                Plan,
                "planning",
                state,
                state.execution_id,
            )
        state.new_chapter = not state.story.chapters or state.plan.start_new_chapter
        if state.new_chapter:
            state.context.directions = [
                direction for direction in state.context.directions if direction.scope != "chapter"
            ]
        return await self.saved(state, "plan")

    async def approval(self, state: WorkflowState) -> WorkflowState:
        if state.plan is None:
            raise ValueError("缺少规划")
        if state.plan.new_character and not state.approved_character and not state.rejected_character:
            answer = ApprovalResponse.model_validate(
                interrupt(
                    {
                        "kind": "character_approval",
                        "need": state.plan.new_character.model_dump(mode="json"),
                        "message": "先同意引入，再编辑确认档案；提交最终 accept/reject 后继续本条图执行。",
                    }
                )
            )
            if answer.decision == "accept":
                if answer.character is None:
                    raise ValueError("批准角色需要档案")
                if any(character.name == answer.character.name for character in state.story.characters):
                    raise ValueError("角色名称已存在")
                state.approved_character = answer.character
            else:
                state.rejected_character = True
        state.approval_phase = None
        state.approval_response = None
        if state.approved_character is not None and not any(
            character.name == state.approved_character.name for character in state.story.characters
        ):
            from ainovel.core.fields import FieldTarget
            from ainovel.core.story_fields import ensure_story_schema, initial_fields

            ensure_story_schema(state.story)
            character = Character.model_validate(state.approved_character.model_dump())
            character.introduced_scene_id = state.scene_id
            state.story.characters.append(character)
            state.story.initial_snapshots.append(
                Snapshot(
                    character_id=character.id,
                    profile=character.profile,
                    aliases=character.aliases,
                    custom_fields=character.custom_fields,
                )
            )
            character.profile.entries = initial_fields(state.story, FieldTarget(scope="character", entity_id=character.id), character.profile.entries)
            baseline = state.story.initial_snapshots[-1]
            baseline.profile = character.profile.model_copy(deep=True)
            baseline.state.entries = initial_fields(state.story, FieldTarget(scope="state", entity_id=character.id), baseline.state.entries)
            state.plan.character_names.append(character.name)
        return await self.saved(state, "approval")

    def characters_route(self, state: WorkflowState) -> str:
        if (
            state.plan
            and state.plan.new_character
            and not state.approved_character
            and not state.rejected_character
        ):
            return "approval"
        if state.intent and state.intent.use_characters and state.story.characters:
            return "approval"
        return "skip_characters"

    async def skip_characters(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "skip_characters", "跳过角色创建和访谈，使用已有档案")
        return await self.saved(state, "skip_characters")

    def after_approval(self, state: WorkflowState) -> str:
        return "consult" if state.intent and state.intent.use_characters and state.story.characters else END

    async def consult(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "consult", "询问角色的想法、边界、外在回应和行动意图")
        if state.plan is None:
            raise ValueError("缺少规划")
        if not state.consultations:
            for character in state.story.characters:
                if character.name not in state.plan.character_names:
                    continue
                snapshot = state.story.snapshot(character.id)
                request = ConsultationInput(
                    character_id=character.id,
                    name=character.name,
                    profile=snapshot.profile,
                    snapshot=SnapshotContext.from_snapshot(snapshot),
                    situation=state.plan.scene_plan,
                    memories=self.retrieval.character(
                        snapshot,
                        state.instruction,
                        [character.name, *[alias.name for alias in snapshot.aliases]],
                    ),
                    instruction=state.feedback.feedback if state.feedback else state.instruction,
                )
                result = await self.model.generate(
                    Consultation,
                    "character_consultation",
                    request,
                    state.execution_id,
                )
                if result.character_id != character.id:
                    raise ValueError("角色咨询返回了错误角色 ID")
                state.consultations.append(result)
        return await self.saved(state, "consult")

    async def write(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "write", "结合人物意图写作当前场景")
        if state.feedback and state.feedback.selection and state.draft_status == "writing":
            if state.draft is None:
                raise ValueError("缺少待修改草稿")
            selection = state.feedback.selection
            original = state.draft.text[selection.start : selection.end]
            if original != selection.selected_text:
                raise ValueError("草稿选区与当前版本不一致")
            result = await self.model.generate(
                RewriteSelectionResult,
                "rewrite_selection",
                DraftRewriteInput(
                    original_context=WritingInput.model_validate(project_context(Draft, state)).model_copy(
                        update={"draft": None, "feedback": None}
                    ),
                    scene=state.draft.text,
                    selection=original,
                    start=selection.start,
                    end=selection.end,
                    instruction=state.feedback.feedback,
                ),
                state.execution_id,
            )
            state.draft.text = (
                state.draft.text[: selection.start] + result.replacement + state.draft.text[selection.end :]
            )
            selection.end = selection.start + len(result.replacement)
            selection.selected_text = result.replacement
            state.draft.summary = result.summary
        elif state.feedback and state.draft_status == "writing":
            if state.draft is None:
                raise ValueError("缺少待修改草稿")
            await self.revise_draft(state)
        elif state.draft is None or state.judge is not None and state.judge.blocked:
            state.draft = await self.model.generate(
                Draft,
                "writing",
                state,
                state.execution_id,
            )
        return await self.saved(state, "write")

    async def revise_draft(self, state: WorkflowState) -> None:
        if state.draft is None:
            raise ValueError("缺少待修改草稿")
        state.edit_validation_feedback = ""
        state.rejected_edits = []
        for attempt in range(1, self.settings.llm_output_retries + 2):
            revision_result = await self.model.generate(
                DraftRevisionResult, "writing", state, state.execution_id
            )
            try:
                revised_text = apply_text_edits(state.draft.text, revision_result.edits)
            except TextEditConflict as error:
                retrying = attempt <= self.settings.llm_output_retries
                state.edit_validation_feedback = str(error)
                state.rejected_edits = revision_result.edits
                await self.store.append_event(
                    Event(
                        command_id=state.execution_id,
                        actor="ai",
                        type="step.failed",
                        trace=Trace(
                            node="draft_edit_validation",
                            summary=f"修改列表定位或范围冲突，正在自动修复（{attempt}/{self.settings.llm_output_retries}）"
                            if retrying
                            else "修改列表仍有冲突，保留原草稿",
                            attempt=attempt,
                            error=str(error),
                            structured_output=revision_result.model_dump_json(),
                        ),
                    )
                )
                if not retrying:
                    raise DraftRevisionError(
                        f"AI 返回的修改片段仍无法安全应用，已自动修复 {attempt - 1} 次。{error}。原草稿未改变，可在运行历史中重试或撤回本次操作。"
                    ) from error
                continue
            state.draft = state.draft.model_copy(
                update={"text": revised_text, "summary": revision_result.summary}
            )
            state.edit_validation_feedback = ""
            state.rejected_edits = []
            return

    def writing_route(self, state: WorkflowState) -> str:
        return "judge" if state.intent is None or state.intent.route == "deliberate" else END

    async def judge(self, state: WorkflowState) -> WorkflowState:
        await self.step(state, "judge", "检查用户要求、创作备注、人物一致性与剧情连续性")
        state.judge = await self.model.generate(
            JudgeResult,
            "judge",
            state,
            state.execution_id,
        )
        if state.judge.blocked:
            state.rewrites += 1
        return await self.saved(state, "judge")

    def after_judge(self, state: WorkflowState) -> str:
        if state.judge is None:
            raise ValueError("缺少审查结果")
        if state.judge.blocked:
            if state.rewrites > self.settings.max_rewrites:
                raise ValueError("达到重写上限，草稿未通过质量检查，未提交正文")
            return "write"
        return END

    async def review(self, state: WorkflowState) -> WorkflowState:
        if state.draft is None:
            raise ValueError("缺少草稿")
        answer = DraftReviewResponse.model_validate(
            interrupt(
                {
                    "kind": "draft_review",
                    "draft": state.draft.model_dump(mode="json"),
                    "version": state.draft_version + 1,
                    "message": "请提出修改意见或确认草稿；确认前不更新正式故事。",
                }
            )
        )
        state.execution_command_id = answer.execution_command_id
        state.text_only_revision = state.text_only_revision or answer.text_only
        state.draft_version += 1
        state.review_response = None
        if answer.decision == "confirm":
            state.draft_status = "confirmed"
        elif answer.decision == "discard":
            state.draft_status = "discarded"
        else:
            state.draft_status = "writing"
            state.extraction = None
            state.maintenance = None
            state.maintenance_checked = False
            state.feedback = answer
            state.revision_plan = None
            state.edit_validation_feedback = ""
            state.rejected_edits = []
            state.judge = None
            state.rewrites = 0
        return state

    def after_review(self, state: WorkflowState) -> str:
        if state.draft_status == "confirmed":
            return "finalize"
        return "planning" if state.draft_status == "writing" else END

    async def extract(self, state: WorkflowState) -> WorkflowState:
        cached = await self.store.checkpoint(state.execution_id)
        if (
            cached
            and cached.extraction is not None
            and cached.scene_id == state.scene_id
            and cached.draft == state.draft
            and cached.draft_status == "confirmed"
        ):
            # The nested graph may re-enter its first node after a later node fails.
            # Reuse the already validated business checkpoint for this exact draft.
            state.extraction = cached.extraction.model_copy(deep=True)
            state.maintenance = cached.maintenance.model_copy(deep=True) if cached.maintenance else None
            state.maintenance_checked = cached.maintenance_checked or cached.maintenance is not None
            await self.step(state, "reuse_extract", "复用已完成的角色提取，继续失败的后续步骤")
            return await self.saved(state, "reuse_extract")
        if state.extraction is not None and state.draft_status == "confirmed":
            await self.step(state, "reuse_extract", "复用同一草稿已验证的角色提取，继续后续步骤")
            return await self.saved(state, "reuse_extract")
        due = maintenance_due(state, self.settings.story_maintenance_interval)
        state.stage_maintenance_due = due
        await self.step(state, "extract", "提取本幕事实" + ("并整理故事阶段进展" if due else ""))
        combined = await self.model.generate(
            FinalizationResult,
            "extraction",
            state,
            state.execution_id,
        )
        state.extraction = combined.extraction
        state.maintenance = combined.maintenance
        state.maintenance_checked = True
        ids = {character.id for character in state.story.characters}
        updated_ids = [update.character_id for update in state.extraction.updates]
        if len(updated_ids) != len(set(updated_ids)) or any(
            identifier not in ids for identifier in updated_ids
        ):
            raise ValueError("提取了重复或未知角色")
        for update in state.extraction.updates:
            if any(experience.scene_id != state.scene_id for experience in update.new_experiences):
                raise ValueError("经历的场景 ID 不符合当前场景")
        return await self.saved(state, "extract")

    async def maintain(self, state: WorkflowState) -> WorkflowState:
        if state.maintenance_checked:
            await self.step(state, "reuse_maintain", "本幕事实与阶段整理已由提取步骤统一检查")
            return await self.saved(state, "reuse_maintain")
        if state.maintenance is not None and state.draft_status == "confirmed":
            await self.step(state, "reuse_maintain", "复用已完成的设定维护，继续提交正文")
            return await self.saved(state, "reuse_maintain")
        await self.step(state, "maintain", "更新世界观、读者偏好、故事进度与作家笔记")
        state.maintenance = await self.model.generate(
            StoryMaintenance,
            "maintenance",
            state,
            state.execution_id,
        )
        return await self.saved(state, "maintain")

    async def run(self, state: WorkflowState) -> WorkflowState:
        await self.checkpoints.initialize()
        if not self.durable_graph:
            self.graph = self.builder.compile(checkpointer=self.checkpoints.saver)
            self.durable_graph = True
        # Library mapping transport is confined to this boundary; business nodes remain typed.
        config = {"configurable": {"thread_id": str(state.command_id)}, "recursion_limit": 50}
        if state.graph_checkpoint_id:
            config["configurable"]["checkpoint_id"] = state.graph_checkpoint_id
        current = await self.graph.aget_state(config)
        if state.execution_command_id is not None:
            latest_attempt = await self.graph.aget_state(
                {"configurable": {"thread_id": str(state.command_id)}}
            )
            if latest_attempt.values.get("execution_command_id") == state.execution_command_id:
                # Recover an interrupted command from its own most recent branch.
                current = latest_attempt
                config = {**latest_attempt.config, "recursion_limit": 50}
        if state.review_response and current.values.get("execution_command_id") != state.execution_command_id:
            # Reusing an older interrupted task would also reuse its already stored
            # resume value. Fork a fresh review task for this new user command,
            # keeping the old gate intact for continuous undo.
            fork = state.model_copy(deep=True)
            fork.draft_version = state.draft_version - 1
            fork.draft_status = "writing"
            config = await self.graph.aupdate_state(config, fork, as_node="writing")
            await self.graph.ainvoke(None, {**config, "recursion_limit": 50})
            current = await self.graph.aget_state({"configurable": {"thread_id": str(state.command_id)}})
            config = {**current.config, "recursion_limit": 50}
            state.graph_checkpoint_id = current.config["configurable"]["checkpoint_id"]
            await self.store.save_checkpoint(state)
        # Older flat-graph checkpoints are rebased once onto the new entry. Cached
        # proposals remain reusable, but every path now passes the draft review gate.
        if current.next and any(
            name not in ("planning", "recall", "characters", "writing", "review", "finalize")
            for name in current.next
        ):
            config = await self.graph.aupdate_state(config, state, as_node=START)
            current = await self.graph.aget_state(config)
        pending = any(task.interrupts for task in current.tasks)
        if pending and state.review_response:
            await self.graph.ainvoke(Command(resume=state.review_response.model_dump(mode="json")), config)
        elif pending and state.approval_response:
            await self.graph.ainvoke(Command(resume=state.approval_response.model_dump(mode="json")), config)
        elif pending:
            pass
        elif current.values:
            if current.next:
                await self.graph.ainvoke(None, config)
        else:
            await self.graph.ainvoke(state, config)
        latest = await self.graph.aget_state({"configurable": {"thread_id": str(state.command_id)}})
        result = WorkflowState.model_validate(latest.values)
        result.graph_checkpoint_id = latest.config["configurable"]["checkpoint_id"]
        interruptions = [item for task in latest.tasks for item in task.interrupts]
        if interruptions:
            if interruptions[0].value["kind"] == "draft_review":
                result.approval_phase = None
                result.draft_status = "pending"
                result.draft_version = interruptions[0].value["version"]
                await self.step(result, "draft_review", "草稿已保存，等待用户修改或确认；尚未更新正式故事")
            else:
                result.approval_phase = "consent"
                await self.step(result, "approval", "已持久化审批检查点，等待用户确认新角色")
        await self.store.save_checkpoint(result)
        return result
