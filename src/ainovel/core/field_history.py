"""Deterministic, ID-addressed patches; snapshots never replace the event journal."""

from copy import deepcopy
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue

from ainovel.core.base import Model
from ainovel.utils.time import now


class PatchPart(Model):
    kind: Literal["key", "item"] = "key"
    value: str


class ValuePatch(Model):
    op: Literal["set", "remove", "order"]
    path: list[PatchPart] = Field(default_factory=list)
    value: JsonValue = None


class FieldChangeEvent(Model):
    revision_id: UUID
    story_id: UUID
    parent_id: UUID | None
    command_id: UUID
    reason: str
    actor: str = "user"
    depth: int = Field(ge=0)
    schema_version: int = 1
    patches: list[ValuePatch]
    created_at: datetime = Field(default_factory=now)


class FieldSnapshot(Model):
    revision_id: UUID
    story_id: UUID
    schema_version: int = 1
    document: JsonValue


def identified(value: list[JsonValue]) -> bool:
    return bool(value) and all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in value)


def diff_values(before: JsonValue, after: JsonValue, path: list[PatchPart] | None = None) -> list[ValuePatch]:
    path = path or []
    if not isinstance(before, (dict, list)) and type(before) is type(after) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        patches = [
            ValuePatch(op="remove", path=[*path, PatchPart(value=key)]) for key in before if key not in after
        ]
        for key, value in after.items():
            target = [*path, PatchPart(value=key)]
            patches.extend(
                diff_values(before[key], value, target)
                if key in before
                else [ValuePatch(op="set", path=target, value=value)]
            )
        return patches
    if (
        isinstance(before, list)
        and isinstance(after, list)
        and (identified(before) or not before)
        and (identified(after) or not after)
    ):
        old = {item["id"]: item for item in before}
        new = {item["id"]: item for item in after}
        if len(old) != len(before) or len(new) != len(after):
            raise ValueError("可寻址列表中存在重复 ID")
        patches = [
            ValuePatch(op="remove", path=[*path, PatchPart(kind="item", value=key)])
            for key in old
            if key not in new
        ]
        for key, value in new.items():
            target = [*path, PatchPart(kind="item", value=key)]
            patches.extend(
                diff_values(old[key], value, target)
                if key in old
                else [ValuePatch(op="set", path=target, value=value)]
            )
        if list(old) != list(new):
            patches.append(ValuePatch(op="order", path=path, value=list(new)))
        return patches
    return [ValuePatch(op="set", path=path, value=after)]


def apply_patches(document: JsonValue, patches: list[ValuePatch]) -> JsonValue:
    result = deepcopy(document)

    def descend(value: JsonValue, part: PatchPart) -> JsonValue:
        if part.kind == "key" and isinstance(value, dict):
            return value[part.value]
        if part.kind == "item" and isinstance(value, list):
            return next(item for item in value if isinstance(item, dict) and item.get("id") == part.value)
        raise ValueError("patch 路径与数据类型不匹配")

    for patch in patches:
        if not patch.path and patch.op == "set":
            result = deepcopy(patch.value)
            continue
        target = result
        for part in patch.path if patch.op == "order" else patch.path[:-1]:
            target = descend(target, part)
        if patch.op == "order":
            if not isinstance(target, list) or not isinstance(patch.value, list):
                raise ValueError("排序 patch 需要列表")
            indexed = {item["id"]: item for item in target}
            if (
                len(indexed) != len(target)
                or set(indexed) != set(patch.value)
                or len(patch.value) != len(indexed)
            ):
                raise ValueError("排序 patch 的列表项不完整")
            target[:] = [indexed[key] for key in patch.value]
            continue
        part = patch.path[-1]
        if part.kind == "key" and isinstance(target, dict):
            if patch.op == "remove":
                del target[part.value]
            else:
                target[part.value] = deepcopy(patch.value)
        elif part.kind == "item" and isinstance(target, list):
            index = next(
                (
                    i
                    for i, item in enumerate(target)
                    if isinstance(item, dict) and item.get("id") == part.value
                ),
                None,
            )
            if patch.op == "remove":
                if index is None:
                    raise ValueError("删除的列表项不存在")
                target.pop(index)
            elif index is None:
                target.append(deepcopy(patch.value))
            else:
                target[index] = deepcopy(patch.value)
        else:
            raise ValueError("patch 路径无效")
    return result
