"""Small named context projections; layout is rendered only through editable Jinja."""

import json
from importlib.resources import files
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from ainovel.core.base import Model


class ContextField(Model):
    """One filled field, without storage definitions or template metadata."""

    id: UUID | None = Field(default=None, description="已有字段值的稳定 ID，可用于定向修改。")
    name: str = Field(description="用户定义的字段名称。")
    value: JsonValue = Field(default=None, description="实际填写的内容。")
    description: str = Field(default="", description="用户提供的字段解释。")
    children: list["ContextField"] = Field(default_factory=list, description="已填写的子字段。")


SECTIONS = {
    "world_context": "世界规则和实际填写的世界资料。",
    "characters_context": "本次相关人物的档案与当前状态。",
    "preferences_context": "用户明确填写的写作偏好。",
    "writer_context": "用户实际配置的创作资料。",
    "scene_plans_context": "可操作的幕及路线信息，保留稳定 ID。",
    "recent_story_context": "近期已确认正文的摘要或本次必要的正文。",
    "task_context": "本次任务所需的请求、草稿、意见等输入。",
}


def default_context_source() -> str:
    return files("ainovel").joinpath("prompts/context.j2").read_text(encoding="utf-8")


def clean(value, key="", depth=0, truncate=True):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if isinstance(value, dict) and {"name", "value"}.issubset(value):
        return {
            k: clean(value[k], k, depth + 1, truncate)
            for k in ("id", "name", "value", "description", "children")
            if k in value and value[k] not in (None, [], {}, "")
        }
    if isinstance(value, dict):
        return {
            k: clean(v, k, depth + 1, truncate)
            for k, v in value.items()
            if k not in {"definition", "field_template_set", "field_documents"}
            and v not in (None, [], {}, "")
        }
    if isinstance(value, list):
        items = [clean(v, key, depth + 1, truncate) for v in (value[:30] if truncate else value)]
        if truncate and len(value) > 30:
            items.append({"omitted_items": len(value) - 30})
        return items
    if (
        truncate
        and isinstance(value, str)
        and key
        not in {"text", "draft_text", "original", "selected_text", "instruction", "message", "request"}
        and len(value) > 2000
    ):
        return value[:2000] + "…（已截取）"
    return value


def sections_from(data: BaseModel | dict) -> dict[str, str]:
    data = clean(data)
    grouped = {}
    for name, keys in {
        "world_context": ("world",),
        "characters_context": ("characters",),
        "preferences_context": ("preferences", "likes", "dislikes"),
        "writer_context": ("writer",),
        "scene_plans_context": ("scene_plans", "active_scene_leaf_id"),
        "recent_story_context": ("recent_story", "recent_scenes", "recent_summaries"),
    }.items():
        section = {key: data.pop(key) for key in keys if key in data}
        grouped[name] = json.dumps(section, ensure_ascii=False, separators=(",", ":")) if section else ""
    # Initial sections are previews; the material tool can retrieve omitted entries.
    for name, encoded in grouped.items():
        if len(encoded) > 6000:
            value = json.loads(encoded)

            def preview(v):
                if isinstance(v, list):
                    return [preview(item) for item in v[:5]] + (
                        [{"omitted_items": len(v) - 5}] if len(v) > 5 else []
                    )
                if isinstance(v, dict):
                    return {k: preview(item) for k, item in v.items()}
                if isinstance(v, str) and len(v) > 300:
                    return v[:300] + "…（完整资料可按需读取）"
                return v

            grouped[name] = json.dumps(
                {"preview": preview(value), "truncated": True}, ensure_ascii=False, separators=(",", ":")
            )
    grouped["task_context"] = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if data else ""
    return grouped


def custom_field_schemas(data, allowed_ids=None):
    """Export each user definition once; values only carry IDs, never repeated schemas."""
    definitions = {}
    fields = {}

    def register(definition):
        identity = str(definition["id"])
        if identity in definitions:
            return {"$ref": "#/$defs/" + identity}
        kind = definition.get("type", "text")
        schema = {
            "title": definition["name"],
            "description": definition.get("description", ""),
            "type": {
                "text": "string",
                "enum": "string",
                "reference": "string",
                "list": "array",
                "object": "object",
                "number": "number",
                "boolean": "boolean",
            }[kind],
        }
        definitions[identity] = schema
        if kind == "reference":
            schema["format"] = "uuid"
        if kind == "enum":
            schema["enum"] = definition.get("options", [])
        if kind == "list" and definition.get("item"):
            schema["items"] = register(definition["item"])
        if kind == "object":
            children = definition.get("children", [])
            schema["properties"] = {child["name"]: register(child) for child in children}
            schema["required"] = [child["name"] for child in children if child.get("required")]
            schema["additionalProperties"] = definition.get("allow_extra", True)
        return {"$ref": "#/$defs/" + identity}

    def visit(value):
        if isinstance(value, dict):
            definition = value.get("definition")
            if definition and value.get("id") and (allowed_ids is None or str(value["id"]) in allowed_ids):
                fields[str(value["id"])] = register(definition)
            for key, item in value.items():
                if key != "definition":
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data.model_dump(mode="json", round_trip=True) if isinstance(data, BaseModel) else data)
    return {"type":"object","description":"按实际字段值 ID 索引的值 schema，字段说明来自用户的定义。","$defs":definitions,"properties":fields} if fields else {}


def transmitted_ids(messages):
    import re
    return set(re.findall(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", "\n".join(message.content for message in messages)))
