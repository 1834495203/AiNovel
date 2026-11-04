"""Prose agent and explicit human draft gate, persisted as ordinary command state."""

from ainovel.core.agent import SubmitDraftAction
from ainovel.core.discussion import DiscussionToolResult
from ainovel.core.domain import Extraction
from ainovel.core.errors import ConflictError
from ainovel.service.agent_loop import context_for, read_tool, run_agent
from ainovel.service.prompt_templates import resolve_command_template


class WritingAgent:
    def __init__(self, model, retrieval, store, settings):
        self.model, self.retrieval, self.store, self.settings = model, retrieval, store, settings

    async def run(self, state):
        review = state.review_response
        if review and review.decision in ("confirm", "discard"):
            state.draft_status = "confirmed" if review.decision == "confirm" else "discarded"
            state.review_response = None
            await self.store.save_checkpoint(state)
            return state
        if not review and state.draft_status in ("pending", "confirmed", "discarded"):
            return state
        state.feedback = review
        state.draft_status = "writing"
        goal = review.feedback if review else state.instruction
        template = next((t for t in state.prompt_templates if t.task == "writing"), None)
        if template is None:
            template = await resolve_command_template(
                self.store, state.execution_id, state.story.id, "writing"
            )
            state.prompt_templates = [template]
        results = [DiscussionToolResult.model_validate(item) for item in state.agent_results]
        # Persist the in-progress attempt, including feedback, before any external call.
        # A failed revision must resume the loop instead of returning the old pending draft.
        await self.store.save_checkpoint(state)

        async def save_results(items):
            state.agent_results = [item.model_dump(mode="json") for item in items]
            await self.store.save_checkpoint(state)

        def build(items):
            return context_for(
                state.story,
                state.source_revision_id or state.command_id,
                "writing",
                goal,
                results=items,
                task_input={
                    "mode": "revise" if review else "write",
                    "scene_id": str(state.scene_id),
                    "scene_plan": state.plan.model_dump(mode="json", exclude_defaults=True)
                    if state.plan
                    else {},
                    "draft": state.draft.model_dump(mode="json") if state.draft else None,
                    "selection": review.selection.model_dump(mode="json")
                    if review and review.selection
                    else None,
                },
                recent=self.settings.recent_scenes,
            )

        async def execute(action):
            return await read_tool(self, state.story, state.source_revision_id, action, state.execution_id)

        result = await run_agent(
            self,
            state.execution_id,
            "writing",
            template,
            build,
            execute,
            results=results,
            on_result=save_results,
        )
        if not isinstance(result, SubmitDraftAction):
            raise ValueError("正文未通过提交工具保存：" + getattr(result, "reply", "请重试"))
        submission = result.arguments
        valid = {c.id for c in state.story.characters}
        if any(update.character_id not in valid for update in submission.updates):
            raise ValueError("正文不能引入未在设定中创建的角色")
        if review and review.selection:
            selection = review.selection
            if not (
                submission.draft.text[: selection.start] == state.draft.text[: selection.start]
                and submission.draft.text.endswith(state.draft.text[selection.end :])
            ):
                raise ConflictError("局部修改超出选区")
        state.draft = submission.draft
        state.extraction = Extraction(updates=submission.updates)
        state.proposed_material_changes = submission.materials
        state.draft_version += 1
        state.draft_status = "pending"
        state.review_response = None
        state.agent_results = []
        await self.store.save_checkpoint(state)
        return state

    async def propose_rewrite(self, services, submission, story, scene, original, instruction, start, end):
        target_id = submission.payload.scene_id or next(
            item.id for item in story.scenes if item.chapter_id == submission.payload.chapter_id
        )
        stage = story.model_copy(deep=True)
        index = next(i for i, item in enumerate(stage.scenes) if item.id == target_id)
        stage.scenes = stage.scenes[:index]
        template = await resolve_command_template(self.store, submission.id, story.id, "writing")

        def build(results):
            return context_for(
                stage,
                submission.base_revision_id,
                "writing",
                instruction,
                results=results,
                task_input={
                    "operation": "rewrite_selection",
                    "scene_id": str(target_id),
                    "scene": scene,
                    "original": original,
                    "start": start,
                    "end": end,
                },
            )

        async def execute(action):
            return await read_tool(services, story, submission.base_revision_id, action, submission.id)

        result = await run_agent(services, submission.id, "writing", template, build, execute)
        if not isinstance(result, SubmitDraftAction):
            raise ValueError("没有提交正文修改结果")
        return result.arguments
