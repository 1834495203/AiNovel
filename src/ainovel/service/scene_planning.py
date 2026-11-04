"""Editable scene plans form a tree; only the selected future path is written."""

from uuid import UUID

from ainovel.core.domain import (
    CommandOutput,
    CommandSubmission,
    Plan,
    PlannedScene,
    PlanSceneRequest,
    SelectSceneBranchRequest,
    Story,
)
from ainovel.service.commands import Handler


def scene_path(story: Story, leaf_id: UUID | None) -> list[PlannedScene]:
    nodes = {node.id: node for node in story.planned_scenes}
    path: list[PlannedScene] = []
    seen: set[UUID] = set()
    while leaf_id is not None:
        if leaf_id not in nodes and any(scene.id == leaf_id for scene in story.scenes):
            break  # Existing prose is a valid anchor for the first new planned scene.
        if leaf_id in seen or leaf_id not in nodes:
            raise ValueError("幕分支不存在或包含循环")
        seen.add(leaf_id)
        node = nodes[leaf_id]
        path.append(node)
        leaf_id = node.parent_id
    return list(reversed(path))


def validate_branch(story: Story, leaf_id: UUID | None) -> list[PlannedScene]:
    path = scene_path(story, leaf_id)
    planned_ids = {node.id for node in story.planned_scenes}
    written = [scene.id for scene in story.scenes if scene.id in planned_ids]
    if [node.id for node in path[: len(written)]] != written:
        raise ValueError("分支不能改变已经写出正文的幕，请从已写部分之后分支")
    return path


def next_scene(story: Story) -> PlannedScene | None:
    written = {scene.id for scene in story.scenes}
    return next(
        (node for node in validate_branch(story, story.active_scene_leaf_id) if node.id not in written), None
    )


class PlanSceneHandler(Handler[PlanSceneRequest]):
    request_type = PlanSceneRequest

    async def execute(self, submission: CommandSubmission, request: PlanSceneRequest) -> CommandOutput:
        story = await self.services.source(submission)
        target = next((node for node in story.planned_scenes if node.id == request.target_id), None)
        if request.target_id is not None and target is None:
            raise ValueError("待修改的幕不存在")
        if target and any(scene.id == target.id for scene in story.scenes):
            raise ValueError("该幕已有正文，设定已锁定，只能重写正文")
        parent_id = target.parent_id if target else request.parent_id
        scene_path(story, parent_id)
        if not target:
            validate_branch(story, parent_id)
        from ainovel.core.discussion import DiscussionToolResult, SaveScenePlansAction
        from ainovel.service.agent_loop import context_for, read_tool, run_agent
        from ainovel.service.prompt_templates import resolve_command_template

        template = await resolve_command_template(
            self.services.store, submission.id, story.id, "scene_planning"
        )

        def build(results):
            return context_for(
                story,
                submission.base_revision_id,
                "scene_planning",
                request.instruction,
                results=results,
                task_input={
                    "target_id": str(request.target_id) if request.target_id else None,
                    "parent_id": str(parent_id) if parent_id else None,
                },
            )

        changed = False

        async def execute(action):
            nonlocal changed
            if isinstance(action, SaveScenePlansAction):
                candidate = story.model_copy(deep=True)
                apply_scene_batch(candidate, action.arguments)
                story.planned_scenes = candidate.planned_scenes
                story.active_scene_leaf_id = candidate.active_scene_leaf_id
                changed = True
                return DiscussionToolResult(
                    name="save_scene_plans",
                    message="幕设定已保存到本轮工作副本",
                    affected_scene_ids=[n.id for n in action.arguments.create],
                )
            return await read_tool(self.services, story, submission.base_revision_id, action, submission.id)

        result = await run_agent(self.services, submission.id, "scene_planning", template, build, execute)
        if not changed:
            raise ValueError("本轮没有提交幕修改：" + getattr(result, "reply", ""))
        return await self.services.commit(submission, story, "保存幕设定")


class SelectSceneBranchHandler(Handler[SelectSceneBranchRequest]):
    request_type = SelectSceneBranchRequest

    async def execute(
        self, submission: CommandSubmission, request: SelectSceneBranchRequest
    ) -> CommandOutput:
        story = await self.services.source(submission)
        validate_branch(story, request.leaf_id)
        story.active_scene_leaf_id = request.leaf_id
        return await self.services.commit(submission, story, "切换后续幕分支")


def apply_scene_batch(story: Story, arguments) -> None:
    """Apply one tool call to an isolated working copy; validate before publishing it."""
    known = {node.id: node for node in story.planned_scenes}
    written = {scene.id for scene in story.scenes}
    if len({item.id for item in arguments.create}) != len(arguments.create):
        raise ValueError("新幕 ID 重复")
    if len({item.id for item in arguments.update}) != len(arguments.update):
        raise ValueError("同一批次不能重复修改同一幕")
    for item in arguments.create:
        if item.id in known or item.id in written:
            raise ValueError("新幕 ID 已存在")
        if item.parent_id is not None and item.parent_id not in known and item.parent_id not in written:
            raise ValueError("父幕不存在，请将父幕排在子幕之前")
        validate_branch(story, item.parent_id)
        node = PlannedScene(
            id=item.id,
            parent_id=item.parent_id,
            title=item.title,
            plan=Plan(
                chapter_plan=item.chapter_title or item.title,
                chapter_title=item.chapter_title,
                scene_plan=item.summary,
                character_names=item.character_names,
                start_new_chapter=item.start_new_chapter,
            ),
        )
        story.planned_scenes.append(node)
        known[node.id] = node
    for item in arguments.update:
        if item.id not in known:
            raise ValueError("待修改的幕不存在")
        if item.id in written:
            raise ValueError("该幕已有正文，设定锁定，只能重写正文")
        node = known[item.id]
        if item.title is not None:
            node.title = item.title
        node.plan.scene_plan = item.summary
        if item.character_names is not None:
            node.plan.character_names = item.character_names
    leaf = arguments.select_leaf_id or (
        arguments.create[-1].id if arguments.create else story.active_scene_leaf_id
    )
    validate_branch(story, leaf)
    story.active_scene_leaf_id = leaf
