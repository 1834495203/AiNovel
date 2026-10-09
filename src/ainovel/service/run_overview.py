"""Typed, replayable projections for a user-visible command and its durable events."""

from __future__ import annotations

from typing import Literal, TypeVar
from uuid import UUID

from pydantic import Field, ValidationError

from ainovel.core.base import Model
from ainovel.core.domain import CommandView, Direction, EventView, Story
from ainovel.core.errors import InvalidCheckpointError
from ainovel.core.records import Entry, Record
from ainovel.mapper.store import Store


class RunStep(Model):
    event_id: int
    command_id: UUID
    node: str
    label: str
    status: Literal["started", "completed", "failed", "waiting", "skipped"]
    explanation: str = ""
    model: str = ""
    prompt_version: str = ""
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    structured_input: str = ""
    structured_output: str = ""
    raw_output: str = ""
    error: str | None = None
    attempts: list["RunAttempt"] = Field(default_factory=list)
    reasoning_content: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    finish_reason: str | None = None
    estimated_usage: bool = False


class RunAttempt(Model):
    model: str = ""
    prompt_version: str = ""
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    error: str | None = None
    structured_input: str = ""
    structured_output: str = ""
    raw_output: str = ""
    reasoning_content: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    finish_reason: str | None = None
    estimated_usage: bool = False


class StepGroup(Model):
    command_id: UUID
    node: str
    occurrence: int
    step: RunStep


class ActiveStep(Model):
    command_id: UUID
    group_index: int


class Change(Model):
    category: Literal["正文", "人物", "世界观", "剧情计划", "创作策略", "其他资料"]
    path: str
    before: str
    after: str


class ChangeSet(Model):
    text_changed: bool = False
    entries: list[Change] = Field(default_factory=list)
    base_revision_id: UUID | None = None
    result_revision_id: UUID | None = None


class RunOverview(Model):
    command: CommandView
    related_commands: list[CommandView] = Field(default_factory=list)
    steps: list[RunStep] = Field(default_factory=list)
    change_set: ChangeSet = Field(default_factory=ChangeSet)
    waiting_for: Literal["角色引入许可", "角色档案确认", "草稿确认"] | None = None
    draft_stale: bool = False
    draft_base_revision_id: UUID | None = None
    current_revision_id: UUID | None = None
    checkpoint_note: str | None = None


_LABELS = {
    "analyze": "理解创作要求与场景规划",
    "plan": "整理本幕计划",
    "quick_plan": "快速整理本幕计划",
    "rewrite_plan": "规划正文修改",
    "retrieve": "查找前文",
    "skip_retrieval": "跳过前文召回",
    "consult": "访谈相关角色",
    "skip_characters": "跳过角色访谈",
    "approval": "等待角色审批",
    "write": "生成正文草稿",
    "draft_edit_validation": "检查草稿修改范围",
    "judge": "审阅草稿",
    "extract": "提取正文中的事实变化",
    "maintain": "更新长期计划",
    "reuse_extract": "复用已提取的角色变化",
    "reuse_maintain": "复用已整理的设定与进度",
    "draft_review": "等待草稿确认",
    "index": "保存正文与资料变化",
    "discussion": "讨论故事资料",
    "search_story": "查找相关正文",
    "update_story_settings": "整理资料修改",
}


_SCHEMA_NODES = {
    "PlanningDecision": "analyze",
    "Plan": "plan",
    "RevisionPlan": "rewrite_plan",
    "Draft": "write",
    "DraftRevisionResult": "write",
    "JudgeResult": "judge",
    "FinalizationResult": "extract",
    "Extraction": "extract",
    "StoryMaintenance": "maintain",
    "RewriteSelectionResult": "write",
}


def _root(command: CommandView) -> UUID:
    payload = command.submission.payload
    return getattr(payload, "generation_command_id", None) or getattr(payload, "proposal_command_id", None) or command.submission.id


def _step_key(item: EventView) -> str | None:
    event = item.event
    if event.trace is None:
        return None
    if event.type.startswith("step."):
        return event.trace.node
    if event.type.startswith("llm."):
        return _SCHEMA_NODES.get(event.trace.node, event.trace.node)
    return None


def _steps(events: list[EventView]) -> list[RunStep]:
    groups: list[StepGroup] = []
    active: list[ActiveStep] = []
    for item in events:
        event = item.event
        trace = event.trace
        if trace is None or not (event.type.startswith("step.") or event.type.startswith("llm.")):
            continue
        if event.type == "step.started":
            active = [item for item in active if item.command_id != event.command_id]
            skipped = trace.node.startswith("skip_") or trace.node.startswith("reuse_")
            initial_status: Literal["started", "completed", "failed", "waiting", "skipped"] = (
                "skipped" if skipped else "started"
            )
            step = RunStep(
                event_id=item.id,
                command_id=event.command_id,
                node=trace.node,
                label=_LABELS.get(trace.node, trace.summary or trace.node),
                status=initial_status,
                explanation=trace.summary,
                structured_input=trace.structured_input,
                structured_output=trace.structured_output,
            )
            groups.append(StepGroup(command_id=event.command_id, node=trace.node, occurrence=1 + sum(
                1 for group in groups if group.command_id == event.command_id and group.node == trace.node
            ), step=step))
            active.append(ActiveStep(command_id=event.command_id, group_index=len(groups) - 1))
            continue
        active_group = next((value for value in active if value.command_id == event.command_id), None)
        node = groups[active_group.group_index].node if active_group and event.type.startswith("llm.") else trace.node
        node = node or _step_key(item)
        if node is None:
            continue
        group_index = active_group.group_index if active_group and groups[active_group.group_index].node == node else None
        if group_index is None:
            group_index = next((index for index in range(len(groups) - 1, -1, -1)
                                if groups[index].command_id == event.command_id and groups[index].node == node), None)
        if group_index is None or event.type.startswith("llm.") and active_group is None:
            skipped = node.startswith("skip_") or node.startswith("reuse_")
            initial_status: Literal["started", "completed", "failed", "waiting", "skipped"] = (
                "skipped" if skipped else "started"
            )
            step = RunStep(
                event_id=item.id,
                command_id=event.command_id,
                node=node,
                label=_LABELS.get(node, trace.summary or node),
                status=initial_status,
                explanation=trace.summary,
            )
            groups.append(StepGroup(command_id=event.command_id, node=node, occurrence=1 + sum(
                1 for group in groups if group.command_id == event.command_id and group.node == node
            ), step=step))
            group_index = len(groups) - 1
        step = groups[group_index].step
        skipped = node.startswith("skip_") or node.startswith("reuse_")
        if event.type == "step.failed":
            step.event_id = item.id
            step.explanation = trace.summary or step.explanation
            step.error = trace.error
            step.structured_input = trace.structured_input or step.structured_input
            step.structured_output = trace.structured_output or step.structured_output
            step.status = "failed"
        elif event.type == "step.completed":
            step.event_id = item.id
            step.explanation = trace.summary or step.explanation
            step.structured_input = trace.structured_input or step.structured_input
            step.structured_output = trace.structured_output or step.structured_output
            step.status = "skipped" if skipped else "completed"
            active = [value for value in active if value.command_id != event.command_id]
        elif event.type in ("llm.completed", "llm.failed"):
            step.event_id = item.id
            step.model = trace.model or step.model
            step.prompt_version = trace.prompt_version or step.prompt_version
            step.duration_ms += trace.duration_ms
            step.input_tokens = (step.input_tokens or 0) + trace.usage.input_tokens if trace.usage.input_tokens is not None else step.input_tokens
            step.output_tokens = (step.output_tokens or 0) + trace.usage.output_tokens if trace.usage.output_tokens is not None else step.output_tokens
            step.cached_tokens = (step.cached_tokens or 0) + trace.usage.cached_tokens if trace.usage.cached_tokens is not None else step.cached_tokens
            step.structured_input = trace.structured_input or step.structured_input
            step.structured_output = trace.structured_output or step.structured_output
            step.raw_output = trace.raw_output or step.raw_output
            step.reasoning_content = trace.reasoning_content or step.reasoning_content
            step.tool_name = trace.tool_name or step.tool_name
            step.tool_call_id = trace.tool_call_id or step.tool_call_id
            step.finish_reason = trace.finish_reason or step.finish_reason
            step.estimated_usage = trace.usage.estimated
            step.attempts.append(RunAttempt(
                model=trace.model,
                prompt_version=trace.prompt_version,
                duration_ms=trace.duration_ms,
                input_tokens=trace.usage.input_tokens,
                output_tokens=trace.usage.output_tokens,
                cached_tokens=trace.usage.cached_tokens,
                error=trace.error,
                structured_input=trace.structured_input,
                structured_output=trace.structured_output,
                raw_output=trace.raw_output,
                reasoning_content=trace.reasoning_content,
                tool_name=trace.tool_name,
                tool_call_id=trace.tool_call_id,
                finish_reason=trace.finish_reason,
                estimated_usage=trace.usage.estimated,
            ))
            if trace.error:
                step.error = trace.error
                step.status = "failed"
            else:
                step.error = None
                step.status = "completed"
    return [group.step for group in groups]


_FIELD_LABELS = {
    "title": "标题",
    "premise": "故事前提",
    "entries": "条目",
    "name": "名称",
    "value": "内容",
    "description": "说明",
    "reason": "依据",
    "children": "子条目",
    "likes": "喜欢的写法",
    "dislikes": "不喜欢的写法",
    "active": "启用状态",
    "origin": "来源",
    "confidence": "置信度",
    "mainlines": "主线",
    "subplots": "支线",
    "foreshadows": "伏笔",
    "outline": "大纲",
    "future_directions": "后续计划",
    "notes": "笔记",
    "drafts": "工作草稿",
    "progress": "整体进度",
    "current_user_intent": "当前创作意图",
    "characters": "人物",
    "profile": "人物资料",
    "aliases": "别名",
    "custom_fields": "自定义资料",
    "state": "当前状态",
    "experiences": "经历",
    "text": "正文",
    "summary": "摘要",
    "chapter_id": "所属章节",
    "scope": "适用范围",
    "instruction": "要求",
    "hard": "强制要求",
    "status": "进度",
    "next_steps": "下一步",
    "order": "顺序",
    "category": "类别",
    "content": "内容",
}

TModel = TypeVar("TModel", bound=Model)


def _identifiers(before: list[TModel], after: list[TModel]) -> list[UUID]:
    identifiers: list[UUID] = []
    for item in [*before, *after]:
        identifier = getattr(item, "id")
        if identifier not in identifiers:
            identifiers.append(identifier)
    return identifiers


def _find(items: list[TModel], identifier: UUID) -> TModel | None:
    return next((item for item in items if getattr(item, "id") == identifier), None)


def _display(value: object) -> str:
    if value is None:
        return "（无）"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        return str(value) if str(value) else "（空）"
    if isinstance(value, UUID):
        return "（关联记录）"
    if isinstance(value, list):
        return "、".join(_display(item) for item in value) if value else "（无）"
    if isinstance(value, Model):
        return "；".join(
            f"{_FIELD_LABELS.get(name, name)}：{_display(getattr(value, name))}"
            for name in type(value).model_fields
            if name not in {"id", "source_scene_id", "character_ids", "depends_on"}
            and getattr(value, name) not in (None, "", [], {})
        ) or value.__class__.__name__
    return str(value)


def _category(path: str) -> Literal["正文", "人物", "世界观", "剧情计划", "创作策略", "其他资料"]:
    first = path.split(".", 1)[0]
    return {
        "正文": "正文",
        "人物": "人物",
        "世界观": "世界观",
        "偏好": "创作策略",
        "剧情计划": "剧情计划",
        "写作要求": "创作策略",
    }.get(first, "其他资料")


def _change(category: str, path: str, before: object, after: object) -> Change:
    return Change(category=_category(category), path=path, before=_display(before), after=_display(after))


def _entries(path: str, before: list[Entry], after: list[Entry], category: str) -> list[Change]:
    result: list[Change] = []
    for identifier in _identifiers(before, after):
        old, new = _find(before, identifier), _find(after, identifier)
        name = (new or old).name  # type: ignore[union-attr]
        item_path = f"{path}／{name}"
        if old is None or new is None:
            result.append(_change(category, item_path, old, new))
        else:
            for field, label in (("name", "名称"), ("value", "内容"), ("description", "说明"), ("reason", "依据")):
                if getattr(old, field) != getattr(new, field):
                    result.append(_change(category, f"{item_path}／{label}", getattr(old, field), getattr(new, field)))
            result.extend(_entries(item_path, old.children, new.children, category))
    return result


def _records(path: str, before: list[Record], after: list[Record], category: str) -> list[Change]:
    result: list[Change] = []
    for identifier in _identifiers(before, after):
        old, new = _find(before, identifier), _find(after, identifier)
        name = (new or old).name  # type: ignore[union-attr]
        item_path = f"{path}／{name}"
        if old is None or new is None:
            result.append(_change(category, item_path, old, new))
        elif old != new:
            result.append(_change(category, item_path, old, new))
    return result


def _story_changes(before: Story, after: Story) -> tuple[list[Change], bool]:
    changes: list[Change] = []
    if before.title != after.title:
        changes.append(_change("其他资料", "作品／标题", before.title, after.title))
    if before.world.premise != after.world.premise:
        changes.append(_change("世界观", "世界观／故事前提", before.world.premise, after.world.premise))
    changes += _entries("世界观／设定", before.world.entries, after.world.entries, "世界观")
    changes += _records("创作策略／喜欢", before.preferences.likes, after.preferences.likes, "创作策略")
    changes += _records("创作策略／避免", before.preferences.dislikes, after.preferences.dislikes, "创作策略")
    changes += _entries("创作策略／自定义", before.preferences.custom_fields, after.preferences.custom_fields, "创作策略")
    for field, label in (
        ("mainlines", "主线"), ("subplots", "支线"), ("foreshadows", "伏笔"), ("outline", "大纲"),
        ("future_directions", "后续计划"), ("notes", "笔记"), ("drafts", "工作草稿"),
    ):
        changes += _records(f"剧情计划／{label}", getattr(before.writer, field), getattr(after.writer, field), "剧情计划")
    for field, label in (("progress", "整体进度"), ("current_user_intent", "当前创作意图")):
        old, new = getattr(before.writer, field), getattr(after.writer, field)
        if old != new:
            changes.append(_change("剧情计划", f"剧情计划／{label}", old, new))
    changes += _entries("其他资料／自定义", before.custom_fields, after.custom_fields, "其他资料")
    changes += _entries("创作策略／备注", before.preferences.custom_fields, after.preferences.custom_fields, "创作策略")
    changes += _directions(before.directions, after.directions)

    for identifier in _identifiers(before.characters, after.characters):
        old, new = _find(before.characters, identifier), _find(after.characters, identifier)
        name = (new or old).name  # type: ignore[union-attr]
        path = f"人物／{name}"
        if old is None or new is None:
            changes.append(_change("人物", path, old, new))
            continue
        if old.name != new.name:
            changes.append(_change("人物", f"{path}／姓名", old.name, new.name))
        if old.profile != new.profile:
            changes.append(_change("人物", f"{path}／人物资料", old.profile, new.profile))
        changes += _records(f"{path}／别名", old.aliases, new.aliases, "人物")
        changes += _entries(f"{path}／自定义", old.custom_fields, new.custom_fields, "人物")
        old_snapshot = before.snapshot(identifier) if any(item.character_id == identifier for item in before.initial_snapshots) else None
        new_snapshot = after.snapshot(identifier) if any(item.character_id == identifier for item in after.initial_snapshots) else None
        if old_snapshot and new_snapshot:
            changes += _entries(f"{path}／状态", old_snapshot.state.entries, new_snapshot.state.entries, "人物")
            for exp_id in _identifiers(old_snapshot.experiences, new_snapshot.experiences):
                old_exp, new_exp = _find(old_snapshot.experiences, exp_id), _find(new_snapshot.experiences, exp_id)
                if old_exp != new_exp:
                    event_name = (new_exp or old_exp).event  # type: ignore[union-attr]
                    changes.append(_change("人物", f"{path}／经历／{event_name}", old_exp, new_exp))

    text_changed = False
    for identifier in _identifiers(before.scenes, after.scenes):
        old, new = _find(before.scenes, identifier), _find(after.scenes, identifier)
        title = (new or old).title  # type: ignore[union-attr]
        path = f"正文／{title}"
        if old is None or new is None:
            text = new.text if new else old.text  # type: ignore[union-attr]
            text_changed = text_changed or bool(text)
            changes.append(_change("正文", path, old.text if old else None, new.text if new else None))
        elif old.text != new.text:
            text_changed = True
            changes.append(_change("正文", f"{path}／正文", old.text, new.text))
        if old is not None and new is not None and old.summary != new.summary:
            changes.append(_change("其他资料", f"{path}／摘要", old.summary, new.summary))
    for identifier in _identifiers(before.chapters, after.chapters):
        old, new = _find(before.chapters, identifier), _find(after.chapters, identifier)
        chapter = (new or old).title  # type: ignore[union-attr]
        if old is None or new is None:
            changes.append(_change("其他资料", f"章节／{chapter}", old, new))
        elif old.title != new.title or old.summary != new.summary:
            changes.append(_change("其他资料", f"章节／{chapter}", old, new))
    return changes, text_changed


def _directions(before: list[Direction], after: list[Direction]) -> list[Change]:
    changes: list[Change] = []
    for identifier in _identifiers(before, after):
        old, new = _find(before, identifier), _find(after, identifier)
        target = new or old
        label = target.instruction if target else ""
        if old != new:
            changes.append(_change("创作策略", f"创作要求／{label}", old, new))
    return changes


async def _all_events(store: Store, command_id: UUID) -> list[EventView]:
    result: list[EventView] = []
    cursor = 0
    while True:
        page = await store.events(command_id, cursor)
        result.extend(page)
        if len(page) < 100:
            return result
        cursor = page[-1].id


async def build_run_overview(store: Store, command_id: UUID) -> RunOverview:
    command = await store.command(command_id)
    commands = await store.commands(command.submission.story_id) if command.submission.story_id else await store.session_commands(command.submission.session_id)
    root = _root(command)
    related = [item for item in commands if _root(item) == root]
    if all(item.submission.id != command_id for item in related):
        related.append(command)
    events: list[EventView] = []
    for item in related:
        events.extend(await _all_events(store, item.submission.id))
    events.sort(key=lambda item: item.id)
    steps = _steps(events)

    result_id: UUID | None = None
    for item in reversed(related):
        # Keep undone children in the event history, but they no longer describe
        # the active root command's committed story state.
        if item.undone and not command.undone:
            continue
        discussion = item.output.discussion
        candidate = discussion.updated_revision_id if discussion and discussion.updated_revision_id else item.output.revision_id
        if candidate is not None:
            result_id = candidate
            break
    baseline_id = command.submission.base_revision_id
    changes: list[Change] = []
    text_changed = False
    if result_id:
        result_revision = await store.revision(result_id)
        # Discussion commits from its actual latest read revision; its output parent is authoritative.
        if result_revision.parent_id is not None:
            baseline_id = result_revision.parent_id
        if baseline_id is not None:
            baseline = await store.revision(baseline_id)
            changes, text_changed = _story_changes(baseline.story, result_revision.story)

    latest_revision = await store.head(command.submission.story_id) if command.submission.story_id else None
    current_revision_id = latest_revision.revision_id if latest_revision else None
    checkpoint = None
    checkpoint_note = None
    try:
        checkpoint = await store.checkpoint(command.submission.id)
    except (ValidationError, InvalidCheckpointError):
        checkpoint_note = "无法读取此旧任务的阶段检查点；命令状态、过程日志和错误仍可查看。"
    waiting_for: Literal["角色引入许可", "角色档案确认", "草稿确认"] | None = None
    stale = False
    draft_base: UUID | None = None
    if checkpoint is not None and command.status == "waiting":
        if checkpoint.approval_phase == "consent":
            waiting_for = "角色引入许可"
        elif checkpoint.approval_phase == "profile":
            waiting_for = "角色档案确认"
        elif checkpoint.draft_status == "pending":
            waiting_for = "草稿确认"
            draft_base = command.submission.base_revision_id
            stale = current_revision_id is not None and draft_base is not None and current_revision_id != draft_base
    if waiting_for is not None:
        node = "draft_review" if waiting_for == "草稿确认" else "approval"
        pending_step = next((step for step in reversed(steps) if step.node == node), None)
        if pending_step is None:
            pending_step = RunStep(
                event_id=max((item.id for item in events), default=0),
                command_id=command.submission.id,
                node=node,
                label=_LABELS[node],
                status="waiting",
            )
            steps.append(pending_step)
        pending_step.status = "waiting"
        pending_step.explanation = (
            "等待确认角色档案"
            if waiting_for == "角色档案确认"
            else "等待角色引入许可"
            if waiting_for == "角色引入许可"
            else "草稿已保存，等待你确认或修改"
        )
    if (
        checkpoint is not None
        and checkpoint.intent is not None
        and checkpoint.intent.route == "fast"
        and not any(step.node == "judge" for step in steps)
    ):
        write_index = next(
            (index for index in range(len(steps) - 1, -1, -1)
             if steps[index].node == "write" and steps[index].status == "completed"),
            None,
        )
        if write_index is not None:
            steps.insert(
                write_index + 1,
                RunStep(
                    event_id=-1 - (command.submission.id.int & ((1 << 62) - 1)),
                    command_id=command.submission.id,
                    node="skip_judge",
                    label="独立草稿审阅",
                    status="skipped",
                    explanation="本次采用快速创作路线，未进行独立审阅",
                ),
            )
    return RunOverview(
        command=command,
        related_commands=related,
        steps=steps,
        change_set=ChangeSet(text_changed=text_changed, entries=changes, base_revision_id=baseline_id, result_revision_id=result_id),
        waiting_for=waiting_for,
        draft_stale=stale,
        draft_base_revision_id=draft_base,
        current_revision_id=current_revision_id,
        checkpoint_note=checkpoint_note,
    )
