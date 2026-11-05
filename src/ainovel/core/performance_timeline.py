"""Forward-only operation paths over one immutable starting state."""

import hashlib
import json
from copy import deepcopy

from ainovel.core.studio import Patch, PerformanceOperation, PerformanceTimeline, StudioNovel
from ainovel.core.studio_state import apply_patch, present, require_editable, role_states

GLOBAL_FIELDS = ("title", "world", "characters", "creation")
SCENE_FIELDS = ("entity", "events", "abandoned", "settings_patches", "character_patches")


def capture_state(novel, scene):
    return {
        **novel.model_dump(mode="json", include=set(GLOBAL_FIELDS)),
        "chapters": {c.entity.id: c.entity.model_dump(mode="json") for c in novel.chapters},
        "scene": scene.model_dump(mode="json", include=set(SCENE_FIELDS)),
    }


def state_hash(state):
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def changes_between(before, after, path=""):
    """Generate only forward JSON Patch; appending b to a is one add operation."""

    def pointer(key):
        return path + "/" + str(key).replace("~", "~0").replace("/", "~1")

    if type(before) is not type(after):
        return [Patch(op="replace", path=path, value=after)]
    changes = []
    if isinstance(before, dict):
        for key in sorted(before.keys() - after.keys()):
            changes.append(Patch(op="remove", path=pointer(key)))
        for key in after:
            if key not in before:
                changes.append(Patch(op="add", path=pointer(key), value=after[key]))
            else:
                changes.extend(changes_between(before[key], after[key], pointer(key)))
    elif isinstance(before, list):
        for index in range(min(len(before), len(after))):
            changes.extend(changes_between(before[index], after[index], pointer(index)))
        for index in range(len(before) - 1, len(after) - 1, -1):
            changes.append(Patch(op="remove", path=pointer(index)))
        for index in range(len(before), len(after)):
            changes.append(Patch(op="add", path=pointer(index), value=after[index]))
    elif before != after:
        changes.append(Patch(op="replace", path=path, value=after))
    return changes


def active_path(timeline, head=None):
    lookup = {op.id: op for op in timeline.operations}
    cursor = timeline.head if head is None else head
    path, visited = [], set()
    while cursor:
        if cursor not in lookup or cursor in visited:
            raise ValueError("演绎操作路径无效")
        visited.add(cursor)
        operation = lookup[cursor]
        path.append(operation)
        cursor = operation.parent
    return list(reversed(path))


def replay(timeline, head=None):
    state = deepcopy(timeline.base)
    for operation in active_path(timeline, head):
        state = apply_patch(state, operation.changes)
    return state


def install_state(novel, scene, state):
    # Validate the entire reconstructed candidate before changing the live aggregate.
    payload = novel.model_dump(mode="json")
    for field in GLOBAL_FIELDS:
        payload[field] = state[field]
    for chapter in payload["chapters"]:
        if chapter["entity"]["id"] in state["chapters"]:
            chapter["entity"] = state["chapters"][chapter["entity"]["id"]]
    for item in payload["scenes"]:
        if item["entity"]["id"] == scene.entity.id:
            item.update(state["scene"])
    candidate = StudioNovel.model_validate(payload)
    candidate_scene = next(s for s in candidate.scenes if s.entity.id == scene.entity.id)
    role_states(candidate, candidate_scene)
    for field in GLOBAL_FIELDS:
        setattr(novel, field, getattr(candidate, field))
    entities = {c.entity.id: c.entity for c in candidate.chapters}
    for chapter in novel.chapters:
        chapter.entity = entities[chapter.entity.id]
    for field in SCENE_FIELDS:
        setattr(scene, field, getattr(candidate_scene, field))
    scene.character_snapshot = None


def record_change(novel, scene, kind, summary, mutate, *, origin="gm", actor_id=""):
    if novel.deleted:
        raise ValueError("请先恢复小说")
    require_editable(novel, scene)
    before = capture_state(novel, scene)
    if scene.timeline is None:
        scene.timeline = PerformanceTimeline(base=before, state_hash=state_hash(before))
    timeline = scene.timeline
    if state_hash(before) != timeline.state_hash:
        raise ValueError("当前资料存在未记录的变更，不能覆盖；请从此幕创建分支后继续")
    candidate = novel.model_copy(deep=True)
    candidate_scene = next(s for s in candidate.scenes if s.entity.id == scene.entity.id)
    result = mutate(candidate, candidate_scene)
    after = capture_state(candidate, candidate_scene)
    changes = changes_between(before, after)
    if not changes:
        return result, None
    known_events = {event.id for event in scene.events}
    operation = PerformanceOperation(
        parent=timeline.head,
        kind=kind,
        summary=summary,
        origin=origin,
        actor_id=actor_id,
        event_ids=[event.id for event in candidate_scene.events if event.id not in known_events],
        changes=changes,
    )
    materialized = apply_patch(before, operation.changes)
    install_state(novel, scene, materialized)
    timeline.operations.append(operation)
    timeline.head = operation.id
    timeline.state_hash = state_hash(materialized)
    return result, operation


def rewind(novel, scene, *, steps=None, target=""):
    if novel.deleted:
        raise ValueError("请先恢复小说")
    require_editable(novel, scene)
    timeline = scene.timeline
    if timeline is None:
        raise ValueError("当前没有可回撤的操作；旧内容未记录操作过程，不能推测其历史状态")
    if state_hash(capture_state(novel, scene)) != timeline.state_hash:
        raise ValueError("当前资料存在未记录的变更，不能覆盖；请从此幕创建分支后继续")
    if not timeline.head and target != "start":
        raise ValueError("已在开始记录的位置，没有更早的步骤可回撤")
    path = active_path(timeline)
    if target:
        if target == "start":
            head = ""
        else:
            found = next((op for op in path if op.id == target or target in op.event_ids), None)
            if found is None:
                raise ValueError("目标不在当前可回溯路径中；只能选择已记录的操作或群聊消息")
            head = found.id
    else:
        count = 1 if steps is None else steps
        if not 1 <= count <= len(path):
            raise ValueError(f"当前路径只有 {len(path)} 步可回撤")
        head = path[-count - 1].id if count < len(path) else ""
    state = replay(timeline, head)
    install_state(novel, scene, state)
    timeline.head = head
    timeline.state_hash = state_hash(state)
    return {
        "head": head,
        "position": len(active_path(timeline)),
        "present": present(scene),
        "present_count": len(present(scene)),
    }


def timeline_context(scene):
    if scene.timeline is None:
        return {"head": "", "position": 0, "start_event_count": len(scene.events), "steps": []}
    path = active_path(scene.timeline)
    return {
        "head": scene.timeline.head,
        "position": len(path),
        "start_event_count": len(scene.timeline.base["scene"]["events"]),
        "steps": [
            {"position": index + 1, **op.model_dump(mode="json", exclude={"changes", "created_at", "parent"})}
            for index, op in enumerate(path)
        ],
    }
