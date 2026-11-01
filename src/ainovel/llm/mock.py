"""Offline model adapter for deterministic integration tests and demos."""

from time import perf_counter
from typing import TypeVar, cast
from uuid import UUID, uuid5

from pydantic import BaseModel

from ainovel.core.characters import Alias, CharacterSpec, Experience, Profile, StatePatch
from ainovel.core.discussion import (
    DiscussionAction,
    DiscussionContext,
    ReplyAction,
    SearchStoryAction,
    SearchStoryArguments,
)
from ainovel.core.domain import (
    ChatResult,
    Consultation,
    Draft,
    Extraction,
    Intent,
    InterviewResult,
    JudgeResult,
    Plan,
    PromptTask,
    PromptTemplateVersion,
    RevisionPlan,
    StateUpdate,
    StoryMaintenance,
    StorySetup,
    Trace,
    Usage,
    WorkflowState,
)
from ainovel.core.ports import TraceSink
from ainovel.core.records import (
    CollectionPatch,
    Entry,
    OutlineBeat,
    PlotThread,
    Preference,
    PreferencePatch,
    StoryMetadataPatch,
    WriterNote,
    WriterPatch,
)
from ainovel.core.text_edits import TextEdit
from ainovel.llm.schemas import (
    DraftRevisionResult,
    FinalizationResult,
    PlanningDecision,
    RewriteSelectionResult,
    TextResult,
    maintenance_due,
)

T = TypeVar("T", bound=BaseModel)


class MockModel:
    """Offline demonstration, deliberately transparent. Not intended to simulate writing quality."""

    def __init__(self, sink: TraceSink) -> None:
        self.sink = sink

    async def choose_discussion_tool(
        self,
        context: DiscussionContext,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> DiscussionAction:
        if not context.tool_results and "召回" in context.message:
            action: DiscussionAction = SearchStoryAction(
                arguments=SearchStoryArguments(query=context.message)
            )
        else:
            action = ReplyAction(
                reply="我们可以继续讨论这个故事的走向。当前世界观："
                + context.story.world.premise
                + ("。本轮工具已完成。" if context.tool_results else "")
            )
        await self.sink.record(
            command_id,
            Trace(
                node="discussion",
                summary="讨论故事与设定",
                model="mock",
                structured_input=context.model_dump_json(),
                structured_output=action.model_dump_json(),
                tool_name=action.kind if not isinstance(action, ReplyAction) else "",
            ),
        )
        return action

    async def generate(
        self,
        schema: type[T],
        task: PromptTask,
        context: BaseModel,
        command_id: UUID,
        *,
        prompt_template: PromptTemplateVersion | None = None,
    ) -> T:
        started = perf_counter()
        result = self._result(schema, context)
        await self.sink.record(
            command_id,
            Trace(
                node=schema.__name__,
                summary="演示模型：" + task[:120],
                model="mock",
                duration_ms=int((perf_counter() - started) * 1000),
                usage=Usage(estimated=True),
                structured_input=context.model_dump_json(),
                structured_output=result.model_dump_json(),
            ),
        )
        return result

    def _result(self, schema: type[T], context: BaseModel) -> T:
        result: BaseModel
        if schema is InterviewResult:
            from ainovel.core.domain import InterviewRequest

            request = cast(InterviewRequest, context)
            count = sum(turn.role == "user" for turn in request.turns)
            questions = [
                "你想写一个什么世界里的故事？可以只给我一个模糊的想法。",
                "你已经想好了哪些角色？没有也可以让我建议。",
                "你喜欢什么样的文风和情节？有哪些不喜欢的叙事方式？",
            ]
            answers = [turn.content for turn in request.turns if turn.role == "user"]
            if count < 3:
                result = InterviewResult(
                    reply=questions[count],
                    proposal=StorySetup(
                        world=answers[0] if answers else "",
                        characters=[
                            CharacterSpec(
                                name="林初", profile=Profile(entries=[Entry(name="来历", value=answers[1])])
                            )
                        ]
                        if count > 1 and "没有角色" not in answers[1]
                        else [],
                    )
                    if count
                    else None,
                )
            else:
                answers = [turn.content for turn in request.turns if turn.role == "user"]
                result = InterviewResult(
                    reply="我整理好了设定，你可以修改后确认创建。",
                    setup=StorySetup(
                        title="雨夜来信",
                        world=answers[0],
                        characters=[
                            CharacterSpec(
                                name="林初",
                                profile=Profile(
                                    entries=[
                                        Entry(name="来历", value=answers[1]),
                                        Entry(name="做事方式", value="谨慎而好奇"),
                                    ]
                                ),
                            )
                        ],
                        preferences=[answers[2]],
                        dislikes=["为推进剧情让人物降智"],
                    ),
                )
        elif schema is CharacterSpec:
            result = CharacterSpec(
                name="周启明",
                profile=Profile(
                    entries=[
                        Entry(name="正在做的事", value="受托调查旧合同"),
                        Entry(name="给人的印象", value="谨慎"),
                    ]
                ),
            )
        elif isinstance(context, WorkflowState):
            state = context
            if schema is PlanningDecision:
                result = PlanningDecision(
                    intent=cast(Intent, self._result(Intent, state)),
                    plan=cast(Plan, self._result(Plan, state)),
                )
            elif schema is FinalizationResult:
                due = (
                    state.stage_maintenance_due
                    if state.stage_maintenance_due is not None
                    else maintenance_due(state)
                )
                result = FinalizationResult(
                    extraction=cast(Extraction, self._result(Extraction, state)),
                    maintenance=cast(StoryMaintenance, self._result(StoryMaintenance, state))
                    if due
                    else None,
                )
            elif schema is Intent:
                result = Intent(
                    goal=state.instruction,
                    constraints=[],
                    route="fast"
                    if (not state.story.characters or state.instruction.strip() in ("继续", "继续写", "续写"))
                    and not any(word in state.instruction for word in ("NPC", "新角色"))
                    else "deliberate",
                    route_reason="复用已有规划" if state.story.writer.mainlines else "根据当前创作复杂度选择",
                    use_retrieval=bool(state.story.scenes)
                    and not (state.feedback or state.instruction.strip() == "继续"),
                    use_characters=bool(state.story.characters)
                    and state.instruction.strip() != "继续"
                    and not state.feedback,
                    retrieval_reason="近期内容足够时无需远期召回",
                    characters_reason="复杂人物情节需要访谈",
                )
            elif schema is RevisionPlan:
                from ainovel.core.domain import CharacterNeed

                result = RevisionPlan(
                    goal=state.feedback.feedback if state.feedback else "修改草稿",
                    instructions=["保留未修改的内容"],
                    new_character=CharacterNeed(name="周启明", role="律师", reason="用户修改草稿时要求引入")
                    if state.feedback
                    and any(word in state.feedback.feedback for word in ("新角色", "NPC"))
                    and not any(c.name == "周启明" for c in state.story.characters)
                    else None,
                    character_names=[c.name for c in state.story.characters],
                )
            elif schema is Plan:
                from ainovel.core.domain import CharacterNeed

                result = Plan(
                    writer_patch=WriterPatch(
                        mainlines=CollectionPatch[PlotThread](
                            upsert=[
                                PlotThread(name="来信的真相", description="从来信疑点逐步揭示人物关系与真相")
                            ]
                        )
                        if not state.story.writer.mainlines
                        else CollectionPatch[PlotThread](),
                        outline=CollectionPatch[OutlineBeat](
                            upsert=[
                                OutlineBeat(name=name, order=index)
                                for index, name in enumerate(("相遇", "建立信任", "揭开真相"))
                            ]
                        )
                        if not state.story.writer.outline
                        else CollectionPatch[OutlineBeat](),
                        current_user_intent=state.instruction,
                    ),
                    chapter_plan="调查雨夜来信",
                    scene_plan=state.instruction,
                    start_new_chapter=(
                        not state.story.chapters
                        or any(
                            term in state.instruction for term in ("新章节", "新一章", "下一章", "另起一章")
                        )
                        or sum(
                            scene.chapter_id == state.story.chapters[-1].id for scene in state.story.scenes
                        )
                        >= 3
                    ),
                    chapter_title="来信的疑点",
                    character_names=[character.name for character in state.story.characters],
                    new_character=(
                        CharacterNeed(name="周启明", role="律师", reason="需要持续出现的调查者")
                        if "NPC" in state.instruction.upper() or "新角色" in state.instruction
                        else None
                    ),
                )
            elif schema is DraftRevisionResult:
                if state.draft is None:
                    raise ValueError("缺少待修改草稿")
                anchor = state.draft.text.split("\n\n")[-1]
                result = DraftRevisionResult(
                    edits=[
                        TextEdit(
                            original=anchor,
                            replacement=anchor + "她重新梳理了眼前的线索，决定谨慎地迈出下一步。",
                            reason="根据修改意见补充本幕行动表现",
                        )
                    ],
                    summary="主角重新梳理来信疑点，并谨慎地采取行动。",
                )
            elif schema is Draft:
                names = [character.name for character in state.story.characters]
                lead = names[0] if names else "她"
                paragraphs = [
                    f"雨停在天亮之前。{lead}把那封没有署名的信放在桌上，纸角还留着潮湿的痕迹。",
                    f"{lead}没有立刻拆开它。窗外的街灯一盏盏熄灭，房间里只剩下纸张摩擦桌面的声音。",
                    "她记得昨天有人说，一切都已经结束。但信封上的日期，偏偏写着明天。",
                    f"{lead}将信收进口袋，决定先去确认一个小小的疑点。她没有告诉任何人自己的打算。",
                ]
                if len(names) > 1:
                    paragraphs.append(f"{names[-1]}在门外停住脚步，没有追问，只递来了一把伞。")
                if names and "绰号" in state.instruction:
                    paragraphs.append(f"信封背面写着熟悉的绰号“小林”，{lead}知道那是在称呼自己。")
                if state.feedback:
                    paragraphs.append("她重新梳理了眼前的线索，决定谨慎地迈出下一步。")
                result = Draft(
                    title="未署名的信",
                    text="\n\n".join(paragraphs),
                    summary="主角收到来信，决定调查日期疑点。",
                )
            elif schema is JudgeResult:
                result = JudgeResult(
                    user_intent_satisfied=True,
                    direction_satisfied=True,
                    issues=[],
                    overall_score=8,
                    should_rewrite=False,
                    rewrite_instructions=[],
                )
            elif schema is Extraction:
                updates: list[StateUpdate] = []
                for character in state.story.characters:
                    updated = StatePatch(
                        entries=CollectionPatch[Entry](
                            upsert=[
                                Entry(
                                    id=uuid5(character.id, name),
                                    name=name,
                                    value=value,
                                    reason="最终正文支持",
                                    source_scene_id=state.scene_id,
                                )
                                for name, value in (
                                    ("眼下的打算", "先核实来信，再决定是否坦白"),
                                    ("这次发现的线索", "信的日期存在异常"),
                                )
                            ]
                        ),
                    )
                    updates.append(
                        StateUpdate(
                            character_id=character.id,
                            state=updated,
                            aliases=CollectionPatch[Alias](
                                upsert=[
                                    Alias(
                                        name="小林", reason="用户指定的绰号", source_scene_id=state.scene_id
                                    )
                                ]
                            )
                            if "绰号" in state.instruction and character == state.story.characters[0]
                            else CollectionPatch[Alias](),
                            reason="最终正文支持这次状态变化",
                            new_experiences=[
                                Experience(
                                    scene_id=state.scene_id,
                                    event="看到日期异常的来信",
                                    entries=[Entry(name="当时的疑问", value="是不是有人刻意留下线索")],
                                    keywords=[character.name, "来信", "日期"],
                                )
                            ],
                        )
                    )
                result = Extraction(updates=updates, decision_summary="依据最终正文提交角色已知信息与经历。")
            elif schema is StoryMaintenance:
                writer = (
                    state.plan.writer_patch.apply(state.story.writer) if state.plan else state.story.writer
                )
                mainlines = [item.model_copy(deep=True) for item in writer.mainlines]
                for item in mainlines:
                    item.status = "active"
                    item.progress = state.draft.summary if state.draft else "继续推进"
                    item.next_steps = ["追踪来信线索，逐步揭示真相"]
                likes = (
                    [
                        Preference(
                            name=state.instruction,
                            reason="用户直接表达",
                            origin="explicit",
                            source_scene_id=state.scene_id,
                        )
                    ]
                    if "喜欢" in state.instruction
                    else []
                )
                dislikes = (
                    [
                        Preference(
                            name=state.instruction,
                            reason="用户直接表达",
                            origin="explicit",
                            source_scene_id=state.scene_id,
                        )
                    ]
                    if "讨厌" in state.instruction
                    else []
                )
                result = StoryMaintenance(
                    patch=StoryMetadataPatch(
                        preferences=PreferencePatch(
                            likes=CollectionPatch[Preference](upsert=likes),
                            dislikes=CollectionPatch[Preference](upsert=dislikes),
                        ),
                        writer=WriterPatch(
                            mainlines=CollectionPatch[PlotThread](upsert=mainlines),
                            progress=state.draft.summary if state.draft else "",
                            current_user_intent=state.instruction,
                            notes=CollectionPatch[WriterNote](
                                upsert=[
                                    WriterNote(
                                        name="本幕连续性",
                                        description=state.draft.summary if state.draft else "",
                                        category="continuity",
                                        source_scene_id=state.scene_id,
                                    )
                                ]
                            ),
                        ),
                    ),
                    decision_summary="维护整体进度和用户意图，保留尚未解决的主线",
                )
            else:
                raise ValueError(f"未实现演示输出 {schema.__name__}")
        elif schema is Consultation:
            result = Consultation(
                character_id=context.character_id,
                entries=[
                    Entry(name="我会怎样回应", value="我再想想"),
                    Entry(name="我在意的事", value="没有证据，不宜贸然坦白"),
                ],
            )
        elif schema is ChatResult:
            result = ChatResult(
                session_id=context.session_id,
                snapshot_id=context.snapshot.id,
                reply="我想先确认自己知道的事情。没有证据之前，我不会轻易下结论。",
            )
        elif schema is TextResult:
            original = getattr(context, "original", "")
            result = TextResult(text=original + "她停了一下，把未说出口的话咽了回去。")
        elif schema is RewriteSelectionResult:
            selection = getattr(context, "selection", "")
            result = RewriteSelectionResult(
                replacement=selection + "她停了一下，把未说出口的话咽了回去。",
                summary="主角重新考虑了眼前的处境，并谨慎地回应。",
            )
        else:
            raise ValueError(f"未实现演示输出 {schema.__name__}")
        return schema.model_validate(result.model_dump())
