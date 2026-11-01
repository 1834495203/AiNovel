from uuid import uuid4

import pytest
from pydantic import BaseModel

from ainovel.core.config import Settings
from ainovel.core.domain import (
    Chapter,
    Consultation,
    ContextChunk,
    Draft,
    DraftReviewResponse,
    DraftSelection,
    Extraction,
    Intent,
    NarrativeContext,
    Plan,
    RevisionPlan,
    Scene,
    Story,
    StoryMaintenance,
    WorkflowState,
)
from ainovel.core.errors import DraftRevisionError
from ainovel.core.records import (
    CollectionPatch,
    Entry,
    Foreshadow,
    OutlineBeat,
    PlotThread,
    Preference,
    StoryMetadataPatch,
    WorldPatch,
    WriterNote,
    WriterPatch,
)
from ainovel.core.text_edits import TextEdit
from ainovel.llm.schemas import (
    AnalysisInput,
    DraftRevisionResult,
    FinalizationInput,
    FinalizationResult,
    PlanningDecision,
    RewriteSelectionResult,
    project_context,
)
from ainovel.service.commands import Services
from ainovel.service.workflow import NarrativeWorkflow


class MemoryStore:
    def __init__(self) -> None:
        self.saved: list[WorkflowState] = []
        self.cached: WorkflowState | None = None

    async def append_event(self, event) -> None:
        return None

    async def save_checkpoint(self, state: WorkflowState) -> None:
        self.saved.append(state.model_copy(deep=True))

    async def checkpoint(self, command_id):
        return self.cached.model_copy(deep=True) if self.cached else None


class FlowModel:
    def __init__(self, *, route: str = "fast", replacement: str = "改后") -> None:
        self.revision = RevisionPlan(goal="按意见修改")
        self.inputs = []
        self.route = route
        self.replacement = replacement
        self.calls: list[type[BaseModel]] = []
        self.world_fact = Entry(name="新事实", value="正文明确出现", reason="正文依据")

    async def generate(self, schema, task, context, command_id):
        self.calls.append(schema)
        self.inputs.append(context.model_copy(deep=True))
        if schema is RevisionPlan:
            return self.revision.model_copy(deep=True)
        if schema is PlanningDecision:
            return PlanningDecision(
                intent=Intent(goal="继续", route=self.route, use_retrieval=False, use_characters=False),
                plan=Plan(
                    chapter_plan="延续当前场景",
                    scene_plan="推进一小步",
                    character_names=[],
                    writer_patch=WriterPatch(current_user_intent="继续"),
                ),
            )
        if schema is Plan:
            return Plan(
                chapter_plan="开启故事",
                scene_plan="引出核心矛盾",
                character_names=[],
                start_new_chapter=True,
                writer_patch=WriterPatch(
                    mainlines=CollectionPatch[PlotThread](upsert=[PlotThread(name="核心主线")]),
                    outline=CollectionPatch[OutlineBeat](upsert=[OutlineBeat(name="开端")]),
                ),
            )
        if schema is Draft:
            return Draft(title="片段", text="新正文", summary="新摘要")
        if schema is DraftRevisionResult:
            return DraftRevisionResult(
                edits=[TextEdit(original="原草稿", replacement="新正文", reason="用户要求")],
                summary="新摘要",
            )
        if schema is RewriteSelectionResult:
            return RewriteSelectionResult(replacement=self.replacement, summary="改写后的事实摘要")
        if schema is FinalizationResult:
            return FinalizationResult(
                extraction=Extraction(updates=[], decision_summary="仅提取正文事实"),
                maintenance=StoryMaintenance(
                    patch=StoryMetadataPatch(
                        world=WorldPatch(entries=CollectionPatch[Entry](upsert=[self.world_fact])),
                        writer=WriterPatch(
                            mainlines=CollectionPatch[PlotThread](
                                upsert=[PlotThread(name="当前线路", progress="本幕推进")]
                            )
                        ),
                    ),
                    decision_summary="记录本幕局部变化",
                ),
            )
        if schema is StoryMaintenance:
            return StoryMaintenance(decision_summary="兼容旧检查点继续维护")
        raise AssertionError(f"unexpected schema {schema.__name__}")


def workflow_for(model: FlowModel) -> tuple[NarrativeWorkflow, MemoryStore]:
    store = MemoryStore()
    workflow = NarrativeWorkflow(model, retrieval=None, store=store, settings=Settings(_env_file=None))
    return workflow, store


def active_story() -> Story:
    chapter = Chapter(title="一章")
    scene = Scene(chapter_id=chapter.id, title="前幕", text="旧正文", summary="前幕摘要")
    return Story(
        title="测试故事",
        world={"premise": "核心世界设定"},
        chapters=[chapter],
        scenes=[scene],
        writer={"mainlines": [PlotThread(name="主线", status="active", progress="已开始")]},
    )


@pytest.mark.asyncio
async def test_fast_scene_uses_three_calls_and_skips_judge_but_keeps_local_facts() -> None:
    model = FlowModel(route="fast")
    workflow, _ = workflow_for(model)
    state = WorkflowState(command_id=uuid4(), story=active_story(), instruction="继续")

    await workflow.analyze(state)
    await workflow.plan(state, quick=True)
    await workflow.write(state)
    assert workflow.writing_route(state) != "judge"
    state.draft_status = "confirmed"
    await workflow.extract(state)
    await workflow.maintain(state)

    assert model.calls == [PlanningDecision, Draft, FinalizationResult]
    assert state.extraction is not None and state.maintenance is not None
    assert state.maintenance.patch.world.entries.upsert == [model.world_fact]
    assert state.maintenance_checked


@pytest.mark.asyncio
async def test_first_scene_defaults_to_deep_and_retains_judge() -> None:
    model = FlowModel(route="fast")
    workflow, _ = workflow_for(model)
    state = WorkflowState(command_id=uuid4(), story=Story(title="测试故事"), instruction="开始")

    await workflow.analyze(state)

    assert state.intent is not None and state.intent.route == "deliberate"
    assert state.plan is not None and state.plan.start_new_chapter
    assert state.plan.writer_patch.mainlines.upsert
    assert state.plan.writer_patch.outline.upsert
    assert model.calls == [PlanningDecision, Plan]
    assert workflow.writing_route(state) == "judge"


def test_prompt_projection_keeps_typed_meaning_and_omits_unrelated_prose() -> None:
    story = active_story()
    world_entry = Entry(name="地点", value="必要地点")
    story.world.entries.append(world_entry)
    story.preferences.likes = [Preference(name="喜欢", description="简洁", origin="explicit")]
    story.preferences.dislikes = [Preference(name="不喜欢", description="跳戏", origin="explicit")]
    foreshadow = Foreshadow(name="伏笔", status="planted", payoff="后续回收")
    beat = OutlineBeat(name="节点", order=4, thread_id=story.writer.mainlines[0].id)
    story.writer.foreshadows = [foreshadow]
    story.writer.outline = [beat]
    story.writer.notes = [WriterNote(name="有效备注", description="保持节奏")]
    story.writer.drafts = [{"name": "无关草稿", "content": "不应注入的长正文", "status": "draft"}]
    state = WorkflowState(command_id=uuid4(), story=story, instruction="继续")
    state.plan = Plan(
        chapter_plan="续写",
        scene_plan="沿地点继续",
        character_names=[],
        writer_patch=WriterPatch(),
    )

    projected = project_context(PlanningDecision, state)
    assert isinstance(projected, AnalysisInput)
    assert projected.world_premise == "核心世界设定"
    assert projected.world[0].id == world_entry.id
    assert projected.likes[0].name == "喜欢" and projected.dislikes[0].name == "不喜欢"
    assert projected.writer.foreshadows[0].id == foreshadow.id
    assert projected.writer.foreshadows[0].payoff == "后续回收"
    assert projected.writer.outline[0].thread_id == story.writer.mainlines[0].id
    assert projected.writer.notes == []
    assert "不应注入的长正文" not in projected.model_dump_json()

    state.draft = Draft(title="确认正文", text="角色做了一件事", summary="摘要")
    finalized = project_context(FinalizationResult, state)
    assert isinstance(finalized, FinalizationInput)
    assert finalized.writer_patch == state.plan.writer_patch
    assert finalized.instruction == "继续"
    assert finalized.writer.foreshadows[0].id == foreshadow.id
    assert finalized.world[0].id == world_entry.id

    state.intent = Intent(goal="重大转折", route="deliberate")
    stage = project_context(FinalizationResult, state)
    assert isinstance(stage, FinalizationInput)
    assert stage.writer.notes[0].id == story.writer.notes[0].id


def test_daily_projection_is_smaller_than_legacy_aggregate_and_omits_old_prose() -> None:
    from ainovel.llm.schemas import NarrativeInput

    story = active_story()
    story.writer.notes = [WriterNote(name=f"旧笔记{i}", description="旧说明" * 100) for i in range(60)]
    story.writer.drafts = [
        {"name": f"旧草稿{i}", "content": "旧正文" * 500, "status": "draft"} for i in range(20)
    ]
    story.writer.mainlines.extend(
        PlotThread(name=f"已完线路{i}", status="resolved", description="很长" * 100) for i in range(50)
    )
    state = WorkflowState(command_id=uuid4(), story=story, instruction="继续")

    compact = project_context(PlanningDecision, state).model_dump_json()
    legacy = NarrativeInput.from_state(state).model_dump_json()
    assert len(compact) < len(legacy)
    assert "旧草稿" not in compact and "旧笔记" not in compact and "已完线路" not in compact


@pytest.mark.asyncio
async def test_confirmation_retry_reuses_cached_combined_finalization() -> None:
    model = FlowModel()
    workflow, store = workflow_for(model)
    state = WorkflowState(
        command_id=uuid4(),
        story=active_story(),
        instruction="继续",
        draft=Draft(title="确认正文", text="事实", summary="摘要"),
        draft_status="confirmed",
    )
    state.extraction = Extraction(updates=[], decision_summary="已提取")
    state.maintenance = StoryMaintenance(decision_summary="已整理")
    state.maintenance_checked = True
    store.cached = state.model_copy(deep=True)

    await workflow.extract(state)
    await workflow.maintain(state)

    assert model.calls == []
    assert state.extraction.decision_summary == "已提取"
    assert state.maintenance.decision_summary == "已整理"


@pytest.mark.asyncio
async def test_legacy_checkpoint_with_extraction_but_no_maintenance_still_resumes() -> None:
    model = FlowModel()
    workflow, store = workflow_for(model)
    state = WorkflowState(
        command_id=uuid4(),
        story=active_story(),
        instruction="继续",
        draft=Draft(title="确认正文", text="事实", summary="摘要"),
        draft_status="confirmed",
    )
    state.extraction = Extraction(updates=[], decision_summary="旧检查点已提取")
    store.cached = state.model_copy(deep=True)

    await workflow.extract(state)
    await workflow.maintain(state)

    assert model.calls == [StoryMaintenance]
    assert state.extraction.decision_summary == "旧检查点已提取"
    assert state.maintenance is not None
    assert state.maintenance.decision_summary == "兼容旧检查点继续维护"


def test_confirmed_commit_keeps_plan_patch_and_source_command_atomic() -> None:
    state = WorkflowState(
        command_id=uuid4(),
        story=active_story(),
        instruction="推进主线",
        draft=Draft(title="新片段", text="正文事实", summary="正文摘要"),
        plan=Plan(
            chapter_plan="当前章",
            scene_plan="推进主线",
            character_names=[],
            writer_patch=WriterPatch(
                mainlines=CollectionPatch[PlotThread](
                    upsert=[PlotThread(name="新主线", description="规划来源")]
                )
            ),
        ),
        extraction=Extraction(updates=[], decision_summary="只写正文事实"),
        maintenance=StoryMaintenance(
            patch=StoryMetadataPatch(
                world=WorldPatch(entries=CollectionPatch[Entry](upsert=[Entry(name="本幕地点")]))
            )
        ),
    )

    committed = Services.apply_scene(state)

    assert any(item.name == "新主线" for item in committed.writer.mainlines)
    assert any(entry.name == "本幕地点" for entry in committed.world.entries)
    assert committed.scenes[-1].source_command_id == state.execution_id


def test_stage_maintenance_uses_chapter_deep_route_and_scene_interval() -> None:
    from ainovel.llm.schemas import maintenance_due

    state = WorkflowState(command_id=uuid4(), story=active_story(), instruction="继续")
    assert not maintenance_due(state, interval=5)
    state.story.scenes.extend(
        Scene(chapter_id=state.story.chapters[0].id, title=f"第{i}", text="x", summary="x") for i in range(3)
    )
    assert maintenance_due(state, interval=5)
    state.story.scenes.pop()
    state.new_chapter = True
    assert maintenance_due(state, interval=99)


@pytest.mark.asyncio
async def test_selection_rewrite_keeps_background_and_only_changes_selected_range() -> None:
    model = FlowModel(replacement="替换片段")
    workflow, _ = workflow_for(model)
    scene_text = "前文选区后文"
    selection_start = 2
    selection_end = 4
    state = WorkflowState(
        command_id=uuid4(),
        story=active_story(),
        instruction="修改这一处",
        draft=Draft(title="片段", text=scene_text, summary="旧摘要"),
        plan=Plan(chapter_plan="当前章", scene_plan="当前场景", character_names=[]),
        feedback=DraftReviewResponse(
            decision="revise",
            execution_command_id=uuid4(),
            feedback="让表达更自然",
            selection=DraftSelection(
                start=selection_start,
                end=selection_end,
                selected_text=scene_text[selection_start:selection_end],
            ),
        ),
    )

    state.context.recent_scenes = state.story.scenes[-1:]
    await workflow.analyze(state)
    assert workflow.planning_route(state) == "rewrite_plan"
    await workflow.rewrite_plan(state)
    await workflow.write(state)

    assert model.calls == [RevisionPlan, RewriteSelectionResult]
    assert model.inputs[-1].original_context.preceding_scene.text == "旧正文"
    assert state.draft is not None
    assert state.draft.text == "前文替换片段后文"
    assert state.draft.summary == "改写后的事实摘要"
    assert state.feedback.selection is not None
    assert state.feedback.selection.selected_text == "替换片段"


@pytest.mark.asyncio
async def test_small_draft_revision_reuses_context_and_skips_expensive_subgraphs() -> None:
    model = FlowModel()
    model.revision.character_names = ["无需重新访谈的角色"]
    workflow, _ = workflow_for(model)
    story = active_story()
    chunk = ContextChunk(
        chapter_id=story.chapters[0].id, ordinal=1, kind="paragraph", content="先前已召回的伏笔"
    )
    state = WorkflowState(
        command_id=uuid4(),
        story=story,
        instruction="原始写作要求",
        intent=Intent(goal="原本目标"),
        plan=Plan(chapter_plan="当前章", scene_plan="原场景", character_names=[]),
        draft=Draft(title="草稿", text="原草稿", summary="摘要"),
        context=NarrativeContext(recent_scenes=story.scenes[-1:], recalled=[chunk]),
        consultations=[Consultation(character_id=uuid4(), entries=[Entry(name="心理", value="原访谈结论")])],
        feedback=DraftReviewResponse(
            decision="revise", execution_command_id=uuid4(), feedback="精简一句措辞"
        ),
    )
    original = state.context.model_copy(deep=True)
    original_consultations = [item.model_copy(deep=True) for item in state.consultations]
    await workflow.prepare_context(state)
    await workflow.analyze(state)
    await workflow.rewrite_plan(state)
    assert state.context == original
    assert state.plan.scene_plan == "原场景"
    assert state.plan.character_names == []
    assert state.consultations == original_consultations
    assert workflow.recall_route(state) == "skip_retrieval"
    assert workflow.characters_route(state) == "skip_characters"
    assert workflow.writing_route(state) == "__end__"
    await workflow.write(state)
    assert model.calls == [RevisionPlan, DraftRevisionResult]
    assert model.inputs[-1].context == original
    projected = project_context(Draft, state)
    assert projected.recalled == [chunk]
    assert projected.consultations == original_consultations
    assert projected.preceding_scene.text == "旧正文"


@pytest.mark.asyncio
async def test_large_revision_can_replan_recall_and_reinterview_without_losing_old_context() -> None:
    model = FlowModel()
    model.revision = RevisionPlan(
        goal="改变本幕冲突", replan=True, use_retrieval=True, use_characters=True, review_required=True
    )
    workflow, _ = workflow_for(model)
    story = active_story()
    old = ContextChunk(chapter_id=story.chapters[0].id, ordinal=1, kind="paragraph", content="原召回")
    added = ContextChunk(chapter_id=story.chapters[0].id, ordinal=2, kind="paragraph", content="补充线索")
    state = WorkflowState(
        command_id=uuid4(),
        story=story,
        instruction="原要求",
        plan=Plan(chapter_plan="原章", scene_plan="旧目标", character_names=[]),
        draft=Draft(title="草稿", text="原草稿", summary="摘要"),
        context=NarrativeContext(recalled=[old]),
        feedback=DraftReviewResponse(
            decision="revise", execution_command_id=uuid4(), feedback="重新安排冲突与人物动机"
        ),
    )
    await workflow.prepare_context(state)
    await workflow.analyze(state)
    await workflow.rewrite_plan(state)
    assert model.calls == [RevisionPlan, Plan]
    assert workflow.recall_route(state) == "retrieve"
    assert state.intent.use_characters
    assert workflow.writing_route(state) == "judge"
    assert state.context.recalled == [old]

    class RetrievalFixture:
        async def narrative(self, revision_id, query, command_id):
            assert query == "重新安排冲突与人物动机"
            return [old, added]

    class CommandFixture:
        async def command(self, command_id):
            from ainovel.core.domain import CommandSubmission, CommandView, GenerateRequest

            return CommandView(
                submission=CommandSubmission(
                    id=command_id,
                    story_id=story.id,
                    base_revision_id=uuid4(),
                    payload=GenerateRequest(instruction="原要求"),
                ),
                status="waiting",
            )

        async def append_event(self, event):
            pass

        async def save_checkpoint(self, state):
            pass

    workflow.retrieval = RetrievalFixture()
    workflow.store = CommandFixture()
    await workflow.retrieve(state)
    assert state.context.recalled == [old, added]


class RepairModel(FlowModel):
    def __init__(self, results: list[DraftRevisionResult]) -> None:
        super().__init__()
        self.results = results

    async def generate(self, schema, task, context, command_id):
        assert schema is DraftRevisionResult
        self.calls.append(schema)
        self.inputs.append(context.model_copy(deep=True))
        return self.results.pop(0)


def pending_revision_state() -> WorkflowState:
    return WorkflowState(
        command_id=uuid4(),
        story=active_story(),
        instruction="原创作要求",
        draft=Draft(title="草稿", text="她很紧张。她轻声说话。", summary="旧摘要"),
        feedback=DraftReviewResponse(
            decision="revise", execution_command_id=uuid4(), feedback="用动作表现紧张"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_edits",
    [
        [
            TextEdit(original="她很紧张。她轻声说话。", replacement="新段落。", reason="修改段落"),
            TextEdit(original="她很紧张。", replacement="她攥住袖口。", reason="修改句子"),
        ],
        [TextEdit(original="不存在的原句。", replacement="新句。", reason="修改表达")],
        [TextEdit(original="。", replacement="！", reason="修改语气")],
    ],
)
async def test_edit_conflicts_are_repaired_using_unchanged_draft_and_original_context(
    invalid_edits: list[TextEdit],
) -> None:
    model = RepairModel(
        [
            DraftRevisionResult(edits=invalid_edits, summary="未应用的摘要"),
            DraftRevisionResult(
                edits=[TextEdit(original="她很紧张。", replacement="她攥住袖口。", reason="动作代替解释")],
                summary="新摘要",
            ),
        ]
    )
    workflow, _ = workflow_for(model)
    state = pending_revision_state()
    state.context.recent_scenes = state.story.scenes[-1:]
    await workflow.write(state)
    assert len(model.calls) == 2
    assert all(item.draft.text == "她很紧张。她轻声说话。" for item in model.inputs)
    assert not model.inputs[0].edit_validation_feedback
    assert model.inputs[1].edit_validation_feedback
    projected = project_context(DraftRevisionResult, model.inputs[1])
    assert projected.edit_validation_feedback == model.inputs[1].edit_validation_feedback
    assert projected.rejected_edits == invalid_edits
    assert projected.preceding_scene.text == "旧正文"
    assert state.draft.text == "她攥住袖口。她轻声说话。"
    assert state.draft.summary == "新摘要"
    assert not state.edit_validation_feedback
    assert not state.rejected_edits


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_limit", [0, 1, 2])
async def test_edit_repair_exhaustion_leaves_original_draft_intact(retry_limit: int) -> None:
    invalid = DraftRevisionResult(
        edits=[TextEdit(original="不存在。", replacement="新句。", reason="修改")], summary="错误摘要"
    )
    model = RepairModel([invalid] * (retry_limit + 1))
    workflow, store = workflow_for(model)
    workflow.settings.llm_output_retries = retry_limit
    state = pending_revision_state()
    original = state.draft.model_copy(deep=True)
    with pytest.raises(DraftRevisionError, match="原草稿未改变"):
        await workflow.write(state)
    assert state.draft == original
    assert len(model.calls) == retry_limit + 1
    assert not store.saved
