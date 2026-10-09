from types import SimpleNamespace
from uuid import uuid4

import pytest

from ainovel.core.characters import Character
from ainovel.core.domain import (
    CommandOutput,
    CommandSubmission,
    CommandView,
    Event,
    EventView,
    GenerateRequest,
    Intent,
    ReviewDraftRequest,
    Revision,
    Story,
    Trace,
    Usage,
    WorkflowState,
)
from ainovel.core.records import Entry, WorldSetting
from ainovel.service.run_overview import _steps, build_run_overview


class MemoryStore:
    def __init__(self, commands, events, revisions, checkpoint=None, head=None):
        self.commands_by_id = {item.submission.id: item for item in commands}
        self.events_by_id = {key: values for key, values in events.items()}
        self.revisions = revisions
        self.saved_checkpoint = checkpoint
        self.latest_head = head

    async def command(self, identifier):
        return self.commands_by_id[identifier]

    async def commands(self, story_id):
        return list(self.commands_by_id.values())

    async def session_commands(self, session_id):
        return list(self.commands_by_id.values())

    async def events(self, identifier, after=0):
        return [item for item in self.events_by_id.get(identifier, []) if item.id > after][:100]

    async def revision(self, identifier):
        return self.revisions[identifier]

    async def head(self, story_id):
        return SimpleNamespace(revision_id=self.latest_head)

    async def checkpoint(self, identifier):
        return self.saved_checkpoint


def command(story_id, base_id, *, status="completed", output=None):
    submission = CommandSubmission(
        story_id=story_id,
        base_revision_id=base_id,
        payload=GenerateRequest(instruction="继续写"),
    )
    return CommandView(submission=submission, status=status, output=output or CommandOutput())


def event(identifier, command_id, event_type, node, **kwargs):
    return EventView(
        id=identifier,
        event=Event(command_id=command_id, type=event_type, trace=Trace(node=node, **kwargs)),
    )


def plain_event(identifier, command_id, event_type):
    return EventView(id=identifier, event=Event(command_id=command_id, type=event_type))


def test_edit_repair_keeps_conflict_visible_and_merges_model_attempts_into_writing():
    command_id = uuid4()
    steps = _steps([
        event(1, command_id, "step.started", "write", summary="修改草稿"),
        event(2, command_id, "llm.completed", "DraftRevisionResult", summary="返回修改列表", model="mock", usage=Usage(input_tokens=10)),
        event(3, command_id, "step.failed", "draft_edit_validation", summary="自动修复修改范围", error="第 1 与第 2 处重叠"),
        event(4, command_id, "llm.completed", "DraftRevisionResult", summary="返回修复后的修改列表", model="mock", usage=Usage(input_tokens=20)),
        event(5, command_id, "step.completed", "write", summary="完成"),
    ])
    assert len(steps) == 2
    assert steps[0].node == "write" and steps[0].status == "completed"
    assert len(steps[0].attempts) == 2 and steps[0].input_tokens == 30
    assert steps[1].label == "检查草稿修改范围" and steps[1].status == "failed"
    assert steps[1].error == "第 1 与第 2 处重叠"


@pytest.mark.asyncio
async def test_text_change_is_separate_from_metadata_and_character_patch_changes():
    story_id, base_id, result_id = uuid4(), uuid4(), uuid4()
    entry_id = uuid4()
    character_id = uuid4()
    before = Story(
        id=story_id,
        title="作品",
        world=WorldSetting(entries=[Entry(id=entry_id, name="旧设定", value="灯塔")]),
        characters=[Character(id=character_id, name="旧名")],
    )
    after = before.model_copy(deep=True)
    after.title = "新标题"
    after.world.premise = "新的前提"
    after.world.entries[0].name = "新设定"
    after.characters[0].name = "林初"
    view = command(story_id, base_id, output=CommandOutput(revision_id=result_id))
    store = MemoryStore(
        [view], {view.submission.id: []},
        {base_id: Revision(id=base_id, story_id=story_id, command_id=uuid4(), reason="base", story=before),
         result_id: Revision(id=result_id, story_id=story_id, parent_id=base_id, command_id=view.submission.id, reason="edit", story=after)},
        head=result_id,
    )
    overview = await build_run_overview(store, view.submission.id)
    assert overview.change_set.text_changed is False
    assert {change.category for change in overview.change_set.entries} == {"其他资料", "世界观", "人物"}
    assert all("／" in change.path for change in overview.change_set.entries)
    assert any(change.path == "世界观／设定／新设定／名称" and change.before == "旧设定" for change in overview.change_set.entries)
    assert any(change.path == "人物／林初／姓名" and change.before == "旧名" for change in overview.change_set.entries)


@pytest.mark.asyncio
async def test_first_generation_reports_text_changes():
    story_id, base_id, result_id = uuid4(), uuid4(), uuid4()
    before = Story(id=story_id, title="作品")
    after = before.model_copy(deep=True)
    from ainovel.core.domain import Chapter, Scene

    chapter = Chapter(title="第一章")
    after.chapters.append(chapter)
    after.scenes.append(Scene(chapter_id=chapter.id, title="门外", text="她没有回答。", summary="停顿"))
    view = command(story_id, base_id, output=CommandOutput(revision_id=result_id))
    store = MemoryStore(
        [view], {view.submission.id: []},
        {base_id: Revision(id=base_id, story_id=story_id, command_id=uuid4(), reason="base", story=before),
         result_id: Revision(id=result_id, story_id=story_id, parent_id=base_id, command_id=view.submission.id, reason="write", story=after)},
        head=result_id,
    )
    overview = await build_run_overview(store, view.submission.id)
    assert overview.change_set.text_changed is True
    assert any(change.category == "正文" and "正文" in change.path for change in overview.change_set.entries)


@pytest.mark.asyncio
async def test_waiting_root_ignores_undone_confirmation_revision_for_change_set():
    story_id, base_id, committed_id = uuid4(), uuid4(), uuid4()
    before = Story(id=story_id, title="作品")
    after = before.model_copy(deep=True)
    after.title = "曾经确认过的标题"
    root = command(story_id, base_id, status="waiting")
    confirmation = CommandView(
        submission=CommandSubmission(
            story_id=story_id,
            base_revision_id=base_id,
            payload=ReviewDraftRequest(
                generation_command_id=root.submission.id,
                expected_draft_version=1,
                decision="confirm",
            ),
        ),
        status="completed",
        output=CommandOutput(revision_id=committed_id),
        undone=True,
    )
    store = MemoryStore(
        [root, confirmation], {root.submission.id: [], confirmation.submission.id: []},
        {
            base_id: Revision(id=base_id, story_id=story_id, command_id=uuid4(), reason="base", story=before),
            committed_id: Revision(id=committed_id, story_id=story_id, parent_id=base_id,
                                   command_id=confirmation.submission.id, reason="confirmed then undone", story=after),
        },
        head=base_id,
    )

    overview = await build_run_overview(store, root.submission.id)

    assert overview.command.undone is False
    assert overview.related_commands == [root, confirmation]
    assert overview.change_set.result_revision_id is None
    assert overview.change_set.text_changed is False
    assert overview.change_set.entries == []


@pytest.mark.asyncio
async def test_waiting_draft_is_not_mislabeled_as_saved_or_stale():
    story_id, base_id = uuid4(), uuid4()
    story = Story(id=story_id, title="作品")
    view = command(story_id, base_id, status="waiting")
    checkpoint = WorkflowState(
        command_id=view.submission.id, story=story, instruction="继续写", draft_status="pending"
    )
    store = MemoryStore(
        [view], {view.submission.id: [event(1, view.submission.id, "step.started", "draft_review", summary="等待"),
                                     event(2, view.submission.id, "step.completed", "draft_review", summary="等待确认")]},
        {base_id: Revision(id=base_id, story_id=story_id, command_id=uuid4(), reason="base", story=story)},
        checkpoint=checkpoint,
        head=base_id,
    )
    overview = await build_run_overview(store, view.submission.id)
    assert overview.waiting_for == "草稿确认"
    assert overview.change_set.text_changed is False
    assert overview.draft_stale is False
    assert overview.steps[-1].status == "waiting"


@pytest.mark.asyncio
async def test_character_profile_approval_is_distinct_from_draft_review():
    story_id, base_id = uuid4(), uuid4()
    story = Story(id=story_id, title="作品")
    view = command(story_id, base_id, status="waiting")
    checkpoint = WorkflowState(
        command_id=view.submission.id,
        story=story,
        instruction="引入同伴",
        approval_phase="profile",
    )
    store = MemoryStore(
        [view], {view.submission.id: [event(1, view.submission.id, "step.started", "approval", summary="审批")]},
        {base_id: Revision(id=base_id, story_id=story_id, command_id=uuid4(), reason="base", story=story)},
        checkpoint=checkpoint,
        head=base_id,
    )
    overview = await build_run_overview(store, view.submission.id)
    assert overview.waiting_for == "角色档案确认"
    assert overview.steps[-1].node == "approval"
    assert overview.steps[-1].status == "waiting"
    assert overview.steps[-1].explanation == "等待确认角色档案"


@pytest.mark.asyncio
async def test_fast_route_shows_skipped_independent_draft_review_after_write():
    story_id, base_id = uuid4(), uuid4()
    story = Story(id=story_id, title="作品")
    view = command(story_id, base_id, status="waiting")
    checkpoint = WorkflowState(
        command_id=view.submission.id,
        story=story,
        instruction="续写",
        intent=Intent(goal="续写", route="fast"),
        draft_status="pending",
    )
    event_rows = [
        event(1, view.submission.id, "step.started", "write", summary="生成正文"),
        event(2, view.submission.id, "llm.completed", "Draft", model="mock", summary="生成草稿"),
        event(3, view.submission.id, "step.completed", "write", summary="完成"),
        event(4, view.submission.id, "step.started", "draft_review", summary="等待确认"),
    ]
    store = MemoryStore(
        [view], {view.submission.id: event_rows},
        {base_id: Revision(id=base_id, story_id=story_id, command_id=uuid4(), reason="base", story=story)},
        checkpoint=checkpoint,
        head=base_id,
    )
    overview = await build_run_overview(store, view.submission.id)
    assert [(step.node, step.status) for step in overview.steps] == [
        ("write", "completed"), ("skip_judge", "skipped"), ("draft_review", "waiting")
    ]
    skipped = overview.steps[1]
    assert skipped.label == "独立草稿审阅"
    assert skipped.explanation == "本次采用快速创作路线，未进行独立审阅"
    assert skipped.event_id < 0
    assert len({step.event_id for step in overview.steps}) == len(overview.steps)


@pytest.mark.asyncio
async def test_failure_trace_tokens_and_event_pagination_are_preserved():
    story_id, base_id = uuid4(), uuid4()
    view = command(story_id, base_id)
    logs = [plain_event(index, view.submission.id, "audit.heartbeat") for index in range(1, 102)]
    logs.extend([
        event(102, view.submission.id, "step.started", "write", summary="生成正文"),
        event(103, view.submission.id, "llm.failed", "Draft", summary="生成草稿", model="mock", duration_ms=85,
              usage=Usage(input_tokens=100, output_tokens=0, cached_tokens=20), error="供应商失败",
              structured_input='{"messages":[{"role":"system","content":"首轮"}]}',
              raw_output='{"error":"bad json"}', tool_name="submit_Draft", tool_call_id="call-1"),
        event(104, view.submission.id, "llm.completed", "Draft", summary="重试成功", model="mock", duration_ms=15,
              usage=Usage(input_tokens=80, output_tokens=40, cached_tokens=10), structured_output="{}",
              reasoning_content="已按schema重新提交", finish_reason="tool_calls"),
        event(105, view.submission.id, "step.completed", "write", summary="完成"),
    ])
    store = MemoryStore([view], {view.submission.id: logs}, {}, head=base_id)
    overview = await build_run_overview(store, view.submission.id)
    assert len(overview.steps) == 1
    step = overview.steps[0]
    assert step.status == "completed"
    assert step.model == "mock"
    assert step.input_tokens == 180
    assert step.output_tokens == 40
    assert step.cached_tokens == 30
    assert step.duration_ms == 100
    assert step.error is None
    assert [attempt.error for attempt in step.attempts] == ["供应商失败", None]
    assert step.attempts[0].structured_input.startswith('{"messages"')
    assert step.attempts[0].raw_output == '{"error":"bad json"}'
    assert step.attempts[0].tool_name == "submit_Draft"
    assert step.reasoning_content == "已按schema重新提交"
    assert step.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_separate_write_judge_write_rounds_remain_separate_steps():
    story_id, base_id = uuid4(), uuid4()
    view = command(story_id, base_id)
    event_rows = []
    index = 1
    for node in ("write", "judge", "write"):
        event_rows.append(event(index, view.submission.id, "step.started", node, summary=node))
        index += 1
        event_rows.append(event(index, view.submission.id, "step.completed", node, summary="完成"))
        index += 1
    store = MemoryStore([view], {view.submission.id: event_rows}, {}, head=base_id)
    overview = await build_run_overview(store, view.submission.id)
    assert [item.node for item in overview.steps] == ["write", "judge", "write"]
