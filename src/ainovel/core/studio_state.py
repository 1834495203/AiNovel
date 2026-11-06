"""Pure transitions: validated patches, role replay and interval-based visibility."""

import json
from copy import deepcopy

from ainovel.core.entities import Entity, visible_entity
from ainovel.core.studio import NamedFieldEdit, Patch, StageEvent, StudioBranch, StudioNovel, StudioScene


def apply_patch(document, operations: list[Patch]):
    result = deepcopy(document)
    for patch in operations:
        if patch.op != "remove" and "value" not in patch.model_fields_set:
            raise ValueError("此操作需要 value")
        if patch.path == "":
            if patch.op == "remove":
                raise ValueError("不允许删除根对象")
            if patch.op == "test":
                if result != patch.value:
                    raise ValueError("test 不匹配")
            else:
                result = deepcopy(patch.value)
            continue
        if not patch.path.startswith("/"):
            raise ValueError("path 必须是 JSON Pointer")
        keys = patch.path[1:].split("/")
        if any("~" in key.replace("~0", "").replace("~1", "") for key in keys):
            raise ValueError("JSON Pointer 转义无效")
        keys = [key.replace("~1", "/").replace("~0", "~") for key in keys]
        parent = result
        try:
            for key in keys[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            key = keys[-1]
            if isinstance(parent, list):
                if key == "-" and patch.op == "add":
                    parent.append(deepcopy(patch.value))
                    continue
                if not key.isdigit() or (len(key) > 1 and key.startswith("0")):
                    raise ValueError("列表索引无效")
                index = int(key)
                if index >= len(parent) + (patch.op == "add"):
                    raise ValueError("列表索引越界")
                if patch.op == "add":
                    parent.insert(index, deepcopy(patch.value))
                elif patch.op == "remove":
                    parent.pop(index)
                elif patch.op == "replace":
                    parent[index] = deepcopy(patch.value)
                elif parent[index] != patch.value:
                    raise ValueError("test 不匹配")
            elif isinstance(parent, dict):
                if patch.op != "add" and key not in parent:
                    raise ValueError("路径不存在")
                if patch.op == "remove":
                    del parent[key]
                elif patch.op == "test":
                    if parent[key] != patch.value:
                        raise ValueError("test 不匹配")
                else:
                    parent[key] = deepcopy(patch.value)
            else:
                raise ValueError("路径不是容器")
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("patch 路径不存在或类型错误") from error
    return result


def apply_field_edits(document, operations: list[NamedFieldEdit | Patch]):
    """Resolve names in the working copy and return the replayable JSON Patch."""
    if all(isinstance(op, Patch) for op in operations):
        return apply_patch(document, operations), operations

    resolved = []
    for op in operations:
        patch = op
        if isinstance(op, NamedFieldEdit):
            if not isinstance(document, dict):
                raise ValueError("按字段名修改时请先指定资料实体")
            matches = []
            for kind, section, key, default in (
                ("description", "description", "name", "value"),
                ("attribute", "special_attributes", "key", "current_value"),
            ):
                entries = document.get(section)
                if op.kind not in (None, kind) or not isinstance(entries, list):
                    continue
                matches.extend(
                    (f"/{section}/{index}", item, default)
                    for index, item in enumerate(entries)
                    if isinstance(item, dict) and item.get(key) == op.field
                )
            if not matches:
                raise ValueError(f"找不到可编辑字段「{op.field}」")
            if len(matches) > 1:
                raise ValueError(f"字段「{op.field}」重名，请用 kind 指定描述或变量")
            path, item, default = matches[0]
            if op.op == "remove_field":
                patch = Patch(op="remove", path=path)
            else:
                prop = op.property or default
                if prop not in item:
                    raise ValueError(f"字段「{op.field}」没有属性「{prop}」")
                escaped = prop.replace("~", "~0").replace("/", "~1")
                patch = Patch(op="replace", path=f"{path}/{escaped}", value=op.value)
        document = apply_patch(document, [patch])
        resolved.append(patch)
    return document, resolved


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
    novel: StudioNovel, scene: StudioScene, character_id: str, operations: list[NamedFieldEdit | Patch]
) -> bool:
    before = role_states(novel, scene)[character_id]
    own = visible_entity(before, own=True)
    if any(
        isinstance(p, Patch)
        and not (p.path.startswith("/description/") or p.path.startswith("/special_attributes/"))
        for p in operations
    ):
        raise ValueError("只能修改自己的描述或变量条目")
    updated, _ = apply_field_edits(own.model_dump(mode="json"), operations)
    candidate = Entity.model_validate(updated)
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
