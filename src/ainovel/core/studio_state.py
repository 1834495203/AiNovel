"""Pure transitions: validated patches, role replay and interval-based visibility."""

import json
import re
from copy import deepcopy

from ainovel.core.entities import Entity, visible_entity
from ainovel.core.studio import (
    Patch,
    StageEvent,
    StudioBranch,
    StudioNovel,
    StudioScene,
)

_MISSING = object()


def _pointer_parts(pointer):
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("无效的 JSON Pointer")
    return [key.replace("~1", "/").replace("~0", "~") for key in pointer[1:].split("/")]


def _array_index(container, key, *, inserting=False):
    if inserting and key == "-":
        return len(container)
    if not re.fullmatch(r"0|[1-9][0-9]*", key):
        raise ValueError("列表索引无效")
    if len(key) > len(str(len(container))):
        raise ValueError("路径不存在（列表索引越界）")
    index = int(key)
    if index >= len(container) + inserting:
        raise ValueError("路径不存在（列表索引越界）")
    return index


def _pointer_value(document, parts):
    if document is _MISSING:
        raise ValueError("路径不存在")
    for key in parts:
        if isinstance(document, list):
            document = document[_array_index(document, key)]
        elif isinstance(document, dict) and key in document:
            document = document[key]
        else:
            raise ValueError("路径不存在")
    return document


def _same_json(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _write_pointer(document, parts, op, value=None):
    if op != "add":
        _pointer_value(document, parts)
    if not parts:
        return _MISSING if op == "remove" else value

    parent = _pointer_value(document, parts[:-1])
    key = parts[-1]
    if isinstance(parent, list):
        index = _array_index(parent, key, inserting=op == "add")
        if op == "add":
            parent.insert(index, value)
        elif op == "remove":
            parent.pop(index)
        else:
            parent[index] = value
    elif isinstance(parent, dict):
        if op == "remove":
            del parent[key]
        else:
            parent[key] = value
    else:
        raise ValueError("目标的父路径不是对象或列表")
    return document


def apply_patch(document, operations: list[Patch]):
    result = deepcopy(document)
    for index, patch in enumerate(operations, start=1):
        op, path = patch.op, patch.path
        try:
            parts = _pointer_parts(path)
            if op == "test":
                if not _same_json(_pointer_value(result, parts), patch.value):
                    raise ValueError("test 不匹配")
            elif op in ("move", "copy"):
                source = _pointer_parts(patch.from_path)
                value = _pointer_value(result, source)
                if op == "move":
                    if source == parts:
                        continue
                    if parts[: len(source)] == source:
                        raise ValueError("不能移动到来源的子路径")
                    result = _write_pointer(result, source, "remove")
                else:
                    value = deepcopy(value)
                result = _write_pointer(result, parts, "add", value)
            else:
                result = _write_pointer(result, parts, op, deepcopy(patch.value))
        except ValueError as error:
            source_label = f"，来源 {patch.from_path!r}" if op in ("move", "copy") else ""
            raise ValueError(f"第 {index} 条 {op} 操作失败（路径 {path!r}{source_label}）。{error}") from error
    return None if result is _MISSING else result


def present(scene: StudioScene) -> list[str]:
    result = []
    for event in scene.events:
        if event.kind == "enter" and event.actor_id not in result:
            result.append(event.actor_id)
        elif event.kind == "leave" and event.actor_id in result:
            result.remove(event.actor_id)
    return result


def emit(scene: StudioScene, kind, text: str, actor_id: str = "", audience=None):
    if audience is None:
        audience = present(scene)
        if kind == "enter" and actor_id not in audience:
            audience.append(actor_id)
    event = StageEvent(kind=kind, text=text, actor_id=actor_id, audience=audience)
    scene.events.append(event)
    return event


def role_states(novel: StudioNovel, target: StudioScene) -> dict[str, Entity]:
    index = next(i for i, scene in enumerate(novel.scenes) if scene.entity.id == target.entity.id)
    states = {c.id: c.model_copy(deep=True) for c in novel.characters}
    start = 0
    # Snapshot is the state after its scene; never use a later snapshot.
    for i in range(index - 1, -1, -1):
        if novel.scenes[i].character_snapshot is not None:
            states.update({k: v.model_copy(deep=True) for k, v in novel.scenes[i].character_snapshot.items()})
            start = i + 1
            break
    for scene in novel.scenes[start : index + 1]:
        for patches in (scene.settings_patches, scene.character_patches):
            for key, operations in patches.items():
                if key not in states:
                    raise ValueError("角色不存在")
                states[key] = Entity.model_validate(
                    apply_patch(states[key].model_dump(mode="json"), operations)
                )
    return states


def role_context(novel: StudioNovel, scene: StudioScene, character_id: str, *, current_direction_id=""):
    states = role_states(novel, scene)
    if character_id not in present(scene):
        raise ValueError("角色不在场")
    history = []
    for item in novel.scenes:
        # Audience is captured at event time: late entry and re-entry cannot reveal gaps.
        events = [
            {"kind": e.kind, "actor_id": e.actor_id, "text": e.text}
            for e in item.events
            if character_id in e.audience and e.id != current_direction_id
        ]
        if events:
            history.append({"scene": item.entity.name, "events": events})
        if item.entity.id == scene.entity.id:
            break
    return {
        "self": visible_entity(states[character_id], own=True).model_dump(mode="json"),
        "history": history,
        "present": [
            visible_entity(states[key], own=False).model_dump(mode="json")
            for key in present(scene)
            if key != character_id
        ],
    }


def describe_self_edit(before: Entity, after: Entity) -> str:
    def display(value):
        return json.dumps(value, ensure_ascii=False)

    before_data = before.model_dump(mode="json")
    after_data = after.model_dump(mode="json")
    changes = []
    for field, key, label in (
        ("description", "name", "描述"),
        ("special_attributes", "key", "变量"),
    ):
        old = {entry[key]: entry for entry in before_data[field]}
        new = {entry[key]: entry for entry in after_data[field]}
        for name in dict.fromkeys([*old, *new]):
            if name not in old:
                changes.append(f"新增{label}「{name}」 {display(new[name])}")
            elif name not in new:
                changes.append(f"删除{label}「{name}」 {display(old[name])}")
            else:
                for prop, value in new[name].items():
                    previous = old[name][prop]
                    if previous != value:
                        changes.append(f"{label}「{name}」 {prop}：{display(previous)} → {display(value)}")
    return "\n".join(changes)


def edit_self(
    novel: StudioNovel, scene: StudioScene, character_id: str, operations: list[Patch]
) -> bool:
    before = role_states(novel, scene)[character_id]
    own = visible_entity(before, own=True)
    candidate = Entity.model_validate(apply_patch(own.model_dump(mode="json"), operations))
    if (candidate.id, candidate.name, candidate.type) != (own.id, own.name, own.type):
        raise ValueError("角色只能更改自身可见资料，不能更改 ID、名字或类型")
    if candidate == own:
        return False
    description = describe_self_edit(own, candidate)
    # Rebuild the visible portion, preserving fields the role was never allowed to read.
    for field, key_name in (("description", "name"), ("special_attributes", "key")):
        hidden = [v for v in getattr(before, field) if v.visibility == 0]
        hidden_keys = {getattr(v, key_name) for v in hidden}
        values = getattr(candidate, field)
        if any(v.visibility == 0 or getattr(v, key_name) in hidden_keys for v in values):
            raise ValueError("不能访问隐藏字段或设置系统可见性")
        setattr(candidate, field, hidden + values)
    patches = [
        Patch(op="replace", path="/description", value=candidate.model_dump(mode="json")["description"]),
        Patch(
            op="replace",
            path="/special_attributes",
            value=candidate.model_dump(mode="json")["special_attributes"],
        ),
    ]
    scene.character_patches.setdefault(character_id, []).extend(patches)
    emit(scene, "self_edit", description or "调整了自身字段顺序", character_id, audience=[character_id])
    return True


def reset(scene: StudioScene):
    if scene.events:
        scene.abandoned.append([e.model_copy(deep=True) for e in scene.events])
    scene.events = []
    scene.character_patches = {}
    scene.character_snapshot = None


def require_editable(novel: StudioNovel, scene: StudioScene):
    if novel.scenes[-1].entity.id != scene.entity.id or scene.confirmed or scene.text:
        raise ValueError("该幕已冻结，请从此幕创建分支")


def require_writable_prose(novel: StudioNovel, scene: StudioScene):
    if not novel.created or not scene.confirmed:
        raise ValueError("请先确认演绎")
    if not scene.text and any(not s.text for s in novel.scenes[: novel.scenes.index(scene)]):
        raise ValueError("请按幕顺序生成正文")


def fork(novel: StudioNovel, scene_id: str):
    index = next((i for i, s in enumerate(novel.scenes) if s.entity.id == scene_id), None)
    if index is None:
        raise ValueError("幕不存在")
    novel.branches.append(
        StudioBranch(
            name=f"原路线 {len(novel.branches) + 1}",
            scenes=deepcopy(novel.scenes),
            chapters=deepcopy(novel.chapters),
        )
    )
    novel.scenes = deepcopy(novel.scenes[: index + 1])
    scene = novel.scenes[-1]
    scene.confirmed = False
    scene.text = ""
    scene.writing_requirements = ""
    scene.writing_messages = []
    reset(scene)
    scene.timeline = None
    kept = {s.chapter_id for s in novel.scenes}
    novel.chapters = [c for c in novel.chapters if c.entity.id in kept]
    for chapter in novel.chapters:
        if chapter.entity.id == scene.chapter_id:
            chapter.completed = False
            chapter.summary_stale = True
    return scene
