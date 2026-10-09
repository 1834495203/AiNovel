"""Readable node projections, independent of LangGraph's checkpoint transport."""

from typing import Literal

from pydantic import Field

from ainovel.core.characters import Character, CharacterSpec
from ainovel.core.domain import (
    Chapter,
    Consultation,
    Draft,
    DraftReviewResponse,
    Extraction,
    Intent,
    JudgeResult,
    Model,
    NarrativeContext,
    Plan,
    RevisionPlan,
    Scene,
    StoryMaintenance,
    WorkflowState,
)
from ainovel.core.records import WorldSetting, WriterWorkspace


class IndexInput(Model):
    title: str
    chapters: list[Chapter]
    scenes: list[Scene]


class NodeData(Model):
    instruction: str | None = None
    feedback: DraftReviewResponse | None = None
    intent: Intent | None = None
    world: WorldSetting | None = None
    writer: WriterWorkspace | None = None
    characters: list[Character] = Field(default_factory=list)
    context: NarrativeContext | None = None
    plan: Plan | None = None
    revision_plan: RevisionPlan | None = None
    consultations: list[Consultation] = Field(default_factory=list)
    draft: Draft | None = None
    judge: JudgeResult | None = None
    extraction: Extraction | None = None
    maintenance: StoryMaintenance | None = None
    approved_character: CharacterSpec | None = None
    draft_status: Literal["writing", "pending", "confirmed", "discarded"] | None = None
    draft_version: int | None = None
    approval_phase: Literal["consent", "profile"] | None = None


def node_input(state: WorkflowState, node: str) -> NodeData:
    match node:
        case "analyze":
            return NodeData(instruction=state.instruction, feedback=state.feedback)
        case "plan" | "quick_plan":
            return NodeData(
                instruction=state.instruction,
                intent=state.intent,
                world=state.story.world,
                writer=state.story.writer,
                context=state.context,
            )
        case "rewrite_plan":
            return NodeData(draft=state.draft, feedback=state.feedback, context=state.context)
        case "retrieve" | "skip_retrieval":
            return NodeData(instruction=state.instruction, intent=state.intent, feedback=state.feedback)
        case "consult" | "skip_characters" | "approval":
            return NodeData(plan=state.plan, characters=state.story.characters, context=state.context)
        case "write":
            return NodeData(
                plan=state.plan,
                context=state.context,
                consultations=state.consultations,
                draft=state.draft,
                feedback=state.feedback,
            )
        case "judge":
            return NodeData(instruction=state.instruction, draft=state.draft, context=state.context)
        case "extract" | "reuse_extract":
            return NodeData(draft=state.draft, characters=state.story.characters)
        case "maintain" | "reuse_maintain":
            return NodeData(
                draft=state.draft,
                world=state.story.world,
                writer=state.story.writer,
                extraction=state.extraction,
            )
        case "draft_review":
            return NodeData(draft=state.draft, draft_version=state.draft_version)
        case _:
            return NodeData(instruction=state.instruction)


def node_output(state: WorkflowState, node: str) -> NodeData:
    match node:
        case "analyze":
            return NodeData(intent=state.intent)
        case "plan" | "quick_plan":
            return NodeData(plan=state.plan)
        case "rewrite_plan":
            return NodeData(revision_plan=state.revision_plan)
        case "retrieve" | "skip_retrieval":
            return NodeData(context=state.context)
        case "consult":
            return NodeData(consultations=state.consultations)
        case "skip_characters":
            return NodeData()
        case "write":
            return NodeData(draft=state.draft)
        case "judge":
            return NodeData(judge=state.judge)
        case "extract" | "reuse_extract":
            return NodeData(extraction=state.extraction)
        case "maintain" | "reuse_maintain":
            return NodeData(maintenance=state.maintenance)
        case _:
            return NodeData(
                approval_phase=state.approval_phase,
                approved_character=state.approved_character,
                draft_status=state.draft_status,
                draft_version=state.draft_version,
            )
