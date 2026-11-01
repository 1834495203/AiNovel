"""The shipped classifications are editable template definitions, not storage columns."""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ainovel.core.fields import FieldDefinition, FieldTemplate, FieldTemplateSet
from ainovel.core.records import (
    Entry,
    Foreshadow,
    OutlineBeat,
    PlotThread,
    Preference,
    WorkingDraft,
    WriterNote,
)


def definition_id(scope: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ainovel:field:{scope}:{key}")


DEFAULT_FIELDS = {
    "story": [],
    "world": [("premise", "世界观简介", "text")],
    "character": [
        ("background", "背景", "text"),
        ("personality", "性格", "text"),
        ("values", "价值观", "text"),
        ("speech_pattern", "表达方式", "text"),
    ],
    "state": [("emotion", "情绪", "text"), ("thoughts", "想法", "text")],
    "preferences": [("likes", "喜欢的写法", "list"), ("dislikes", "不喜欢的写法", "list")],
    "writer": [
        ("mainlines", "主线", "list"),
        ("subplots", "支线", "list"),
        ("foreshadows", "伏笔", "list"),
        ("outline", "大纲", "list"),
        ("future_directions", "未来方向", "list"),
        ("notes", "创作笔记", "list"),
        ("drafts", "工作资料", "list"),
        ("progress", "写作进度", "text"),
        ("current_user_intent", "当前创作意图", "text"),
    ],
    "chapter": [],
    "scene": [],
}
LABELS = {
    "story": "小说资料",
    "world": "世界观",
    "character": "角色档案",
    "state": "角色状态",
    "preferences": "创作偏好",
    "writer": "创作工作区",
    "chapter": "章节资料",
    "scene": "幕资料",
}

FIELD_HELP = {
    "premise": "概括故事发生的世界、核心规则和基本矛盾。",
    "background": "记录人物的出身、成长经历、身份和重要关系。",
    "personality": "描述人物稳定的性格，以及遇到压力时通常如何反应。",
    "values": "记录人物重视什么、坚守什么，以及不愿跨越的底线。",
    "speech_pattern": "描述人物的语气、常用措辞和说话习惯。",
    "emotion": "记录人物在当前故事阶段的情绪及其原因。",
    "thoughts": "记录人物当前的想法、判断和打算，区分猜测与已知事实。",
    "likes": "记录希望作品采用的文风、节奏、情节或人物写法。",
    "dislikes": "记录希望避免的写法、情节套路或表现方式。",
    "mainlines": "记录贯穿全书的核心矛盾、推进进度和后续计划。",
    "subplots": "记录围绕人物或局部事件展开的支线及其进展。",
    "foreshadows": "记录准备埋下、已经埋下或已经回收的线索，以及回收安排。",
    "outline": "按顺序列出后续情节节点，并记录各节点的完成状态。",
    "future_directions": "保存尚未发生的剧情方向和备选方案，不作为既成事实。",
    "notes": "保存创作提醒、待解决问题和需要长期记住的事项。",
    "drafts": "保存备用构思、大纲片段或场景工作资料，独立于正式正文。",
    "progress": "概括已确认的故事写到哪里，以及当前正在推进的情节。",
    "current_user_intent": "记录用户最近明确提出的创作目标和要求。",
}

RECORD_HELP = {
    "name": "为这条资料起一个简短、便于识别的名称。",
    "description": "详细说明这条资料的内容，与名称互相补充。",
    "reason": "说明新增或修改这条资料的原因，以及依据的正文或用户要求。",
    "source_scene_id": "填写这条资料来源的幕 ID；尚无对应幕时留空。",
    "origin": "偏好的来源：explicit 表示用户明确提出，inferred 表示根据交流推测。",
    "confidence": "对这条偏好判断的把握程度，使用 0 到 1 的数字。",
    "active": "是否让这条偏好在后续写作中生效。",
    "progress": "记录这条剧情线已经实际推进到哪一步。",
    "next_steps": "列出接下来计划推进的动作；这些内容尚未在故事中发生。",
    "character_ids": "填写与这条剧情线相关的角色 ID 列表。",
    "depends_on": "填写这条剧情线需要依赖或先完成的其他剧情线 ID。",
    "payoff": "说明这条伏笔打算如何揭示或回收。",
    "planted_scene_id": "填写伏笔实际埋下时所在的幕 ID；尚未埋下时留空。",
    "paid_off_scene_id": "填写伏笔实际回收时所在的幕 ID；尚未回收时留空。",
    "order": "大纲节点的先后顺序，使用从 0 开始的数字。",
    "thread_id": "填写这个大纲节点关联的剧情线 ID；没有关联时留空。",
    "category": "为笔记填写便于整理的分类，例如人物动机、节奏或待解决问题。",
    "kind": "工作资料的种类：scene 为场景，outline 为大纲，idea 为构思。",
    "content": "填写这份工作资料的具体内容；不会直接替换正式正文。",
}
STATUS_HELP = {
    "mainlines": "剧情线状态：planned 待推进，active 推进中，resolved 已解决，abandoned 已放弃。",
    "subplots": "剧情线状态：planned 待推进，active 推进中，resolved 已解决，abandoned 已放弃。",
    "future_directions": "方向状态：planned 待考虑，active 正在推进，resolved 已完成，abandoned 已放弃。",
    "foreshadows": "伏笔状态：planned 待埋，planted 已埋下，paid_off 已回收，dropped 不再使用。",
    "outline": "节点状态：planned 待推进，active 进行中，completed 已完成，abandoned 已放弃。",
    "drafts": "资料状态：draft 待使用，used 已使用，discarded 已放弃。",
}


def record_definition(scope: str, key: str) -> FieldDefinition | None:
    model = {
        "likes": Preference,
        "dislikes": Preference,
        "mainlines": PlotThread,
        "subplots": PlotThread,
        "future_directions": PlotThread,
        "foreshadows": Foreshadow,
        "outline": OutlineBeat,
        "notes": WriterNote,
        "drafts": WorkingDraft,
    }.get(key)
    if model is None:
        return None
    schema = model.model_json_schema()

    def field(name: str, raw: dict, path: str) -> FieldDefinition:
        if "anyOf" in raw:
            raw = next((item for item in raw["anyOf"] if item.get("type") != "null"), {})
        kind = (
            "enum"
            if raw.get("enum")
            else "reference"
            if raw.get("format") == "uuid"
            else {
                "integer": "number",
                "number": "number",
                "boolean": "boolean",
                "array": "list",
            }.get(raw.get("type"), "text")
        )
        return FieldDefinition(
            id=definition_id(scope, path),
            name=name,
            type=kind,
            description=raw.get("description") or (
                STATUS_HELP.get(key, "") if name == "status" else
                "引用已有对象的 ID。" if name == "列表项" and kind == "reference" else
                "一项后续推进计划。" if name == "列表项" and path.startswith(key + ".item.next_steps.") else
                RECORD_HELP.get(name, "")
            ),
            required=name == "name",
            options=raw.get("enum", []),
            item=field("列表项", raw.get("items", {}), path + ".item") if kind == "list" else None,
        )

    return FieldDefinition(
        id=definition_id(scope, key + ".item"),
        name="资料项",
        type="object",
        children=[
            field(name, raw, key + ".item." + name)
            for name, raw in schema["properties"].items()
            if name not in ("id", "custom_fields")
        ],
        allow_extra=True,
    )


def default_templates() -> list[FieldTemplate]:
    return [
        FieldTemplate(
            id=uuid5(NAMESPACE_URL, f"ainovel:default-template:{scope}"),
            name=f"默认 · {LABELS[scope]}",
            scope=scope,
            builtin=True,
            description=f"用于初始化{LABELS[scope]}的字段结构。",
            fields=[
                FieldDefinition(
                    id=definition_id(scope, key),
                    name=label,
                    type=kind,
                    description=FIELD_HELP.get(key, ""),
                    item=record_definition(scope, key) if kind == "list" else None,
                )
                for key, label, kind in fields
            ],
        )
        for scope, fields in DEFAULT_FIELDS.items()
    ]


def default_definition(scope: str, key: str) -> FieldDefinition:
    return next(
        field
        for template in default_templates()
        if template.scope == scope
        for field in template.fields
        if field.id == definition_id(scope, key)
    )


def instantiate(definition: FieldDefinition, namespace: UUID | None = None) -> Entry:
    return Entry(
        id=uuid5(namespace, str(definition.id)) if namespace else uuid4(),
        name=definition.name,
        definition=definition.model_copy(deep=True),
        children=[instantiate(child, namespace) for child in definition.children],
    )


def merge_template(entries: list[Entry], template: FieldTemplate, namespace: UUID) -> list[Entry]:
    """Add missing definitions without replacing any existing value or pinned definition."""
    result = [entry.model_copy(deep=True) for entry in entries]
    for definition in template.fields:
        found = next(
            (entry for entry in result if entry.definition and entry.definition.id == definition.id), None
        )
        if found is None:
            result.append(instantiate(definition, namespace))
    return result


def default_template_set() -> FieldTemplateSet:
    return FieldTemplateSet(
        id=uuid5(NAMESPACE_URL, "ainovel:default-template-set"), name="默认小说模板",
        description="世界观、角色、偏好、创作计划、章节和幕的完整初始化结构。",
        templates=default_templates(), builtin=True,
    )


def copy_template_set(source: FieldTemplateSet, name: str | None = None) -> FieldTemplateSet:
    """Freeze an independent full copy; no future operation resolves a library ID."""
    result = source.model_copy(deep=True)
    result.id = uuid4()
    result.parent_id = source.id
    result.name = name or source.name
    result.builtin = False
    result.replaces_id = None
    existing = {item.scope for item in result.templates}
    result.templates.extend(item for item in default_templates() if item.scope not in existing)
    for template in result.templates:
        template.parent_id = template.id
        template.id = uuid4()
        template.builtin = False
    return result


def definition_from_entry(entry: Entry) -> FieldDefinition:
    """Extract current structure, never filled values, notes or source scene IDs."""
    definition = entry.definition.model_copy(deep=True) if entry.definition else FieldDefinition(
        id=entry.id, name=entry.name,
        type="object" if entry.children else "boolean" if isinstance(entry.value, bool)
        else "number" if isinstance(entry.value, (int, float)) else "text",
    )
    definition.name = entry.name
    if definition.type == "object":
        definition.children = [definition_from_entry(child) for child in entry.children]
    elif definition.type == "list" and entry.children and definition.item:
        # List item names/values describe story content, not a new field name.
        item = definition_from_entry(entry.children[0])
        item.name = definition.item.name
        definition.item = item
    return definition
