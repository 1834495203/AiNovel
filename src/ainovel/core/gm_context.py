"""The GM's read-only template data and its compatibility view."""

from typing import Literal

from pydantic import Field

from ainovel.core.base import Model
from ainovel.core.entities import Entity
from ainovel.core.performance_timeline import timeline_context
from ainovel.core.studio import ConversationMessage, StageEvent, StudioNovel, StudioScene
from ainovel.core.studio_state import present, role_states


class CharacterRef(Model):
    id: str
    name: str


class PerformanceEventContext(Model):
    id: str
    position: int
    kind: Literal["gm", "gm_direction", "role", "enter", "leave", "self_edit"]
    label: str
    speaker: str | None = Field(description="实际发言者的显示名。进出场等记录可以没有发言者。")
    character: CharacterRef | None = Field(description="事件涉及的角色。GM 调度提示词里指接收提示的角色。")
    audience: list[CharacterRef] = Field(description="事件发生时有权收到它的角色。")
    text: str = Field(description="保留演绎原话。")


class RewindStepContext(Model):
    position: int
    id: str
    kind: str
    summary: str
    origin: Literal["gm", "role", "user", "writer"]
    actor_id: str
    event_ids: list[str]


class RewindContext(Model):
    head: str
    position: int
    start_event_count: int
    steps: list[RewindStepContext]


class NovelContext(Model):
    title: str
    world: list[Entity]
    creation: list[Entity]


class SceneContext(Model):
    settings: Entity
    performance: list[PerformanceEventContext]
    prose: str | None = Field(description="本幕已写正文，幕级前情中省略。")


class ChapterContext(Model):
    id: str
    name: str
    level: Literal["full", "scenes", "summary"] = Field(description="完整内容、仅演绎、仅摘要。")
    summary: str | None = Field(description="仅摘要级前情填入。")
    scenes: list[SceneContext] = Field(description="前情幕列表，摘要级为空。")


class CurrentSceneContext(SceneContext):
    chapter_id: str
    chapter_name: str
    present: list[CharacterRef]
    rewind: RewindContext


class DiscussionMessageContext(Model):
    role: Literal["user", "assistant"]
    speaker: str
    text: str


class GmContext(Model):
    novel: NovelContext = Field(description="小说名称、世界观和全书创作要求。")
    characters: list[Entity] = Field(description="当前幕的正式角色名册和对应状态。")
    current_scene: CurrentSceneContext = Field(description="当前幕的设定、演绎、在场角色和回撤目录。")
    chapter_scenes: list[Entity] = Field(description="当前章节的幕目录。")
    chapters: list[ChapterContext] = Field(description="当前幕之前的故事，按压缩设置分层。")
    discussion: list[DiscussionMessageContext] = Field(description="本幕较早的幕后讨论，按时间顺序排列。")


EVENT_LABELS = {
    "gm": "GM 群聊",
    "gm_direction": "调度提示词",
    "role": "角色发言或行动",
    "enter": "入场",
    "leave": "离场",
    "self_edit": "角色字段变化",
}


def gm_context(
    novel: StudioNovel,
    scene: StudioScene,
    discussion: list[ConversationMessage] | None = None,
) -> GmContext:
    states = role_states(novel, scene)
    roster = {key: CharacterRef(id=key, name=value.name) for key, value in states.items()}

    def reference(key: str) -> CharacterRef:
        return roster[key] if key in roster else CharacterRef(id=key, name=key)

    def describe_event(event: StageEvent, position: int) -> PerformanceEventContext:
        character = reference(event.actor_id) if event.actor_id else None
        speaker = "GM" if event.kind in ("gm", "gm_direction") else None
        if event.kind == "role" and character:
            speaker = character.name
        return PerformanceEventContext(
            id=event.id,
            position=position,
            kind=event.kind,
            label=EVENT_LABELS[event.kind],
            speaker=speaker,
            character=character,
            audience=[reference(key) for key in event.audience],
            text=event.text,
        )

    def performance(item: StudioScene) -> list[PerformanceEventContext]:
        return [describe_event(event, index) for index, event in enumerate(item.events, start=1)]

    earlier: dict[str, list[StudioScene]] = {}
    for item in novel.scenes:
        if item.entity.id == scene.entity.id:
            break
        earlier.setdefault(item.chapter_id, []).append(item)
    target_index = next(
        i for i, chapter in enumerate(novel.chapters) if chapter.entity.id == scene.chapter_id
    )
    chapters = []
    for index, chapter in enumerate(novel.chapters[: target_index + 1]):
        distance = target_index - index
        if distance < novel.compression.full_chapters or chapter.summary_stale or not chapter.summary:
            level = "full"
        elif distance < novel.compression.full_chapters + novel.compression.scene_chapters:
            level = "scenes"
        else:
            level = "summary"
        chapters.append(
            ChapterContext(
                id=chapter.entity.id,
                name=chapter.entity.name,
                level=level,
                summary=chapter.summary if level == "summary" else None,
                scenes=[]
                if level == "summary"
                else [
                    SceneContext(
                        settings=item.entity,
                        performance=performance(item),
                        prose=item.text if level == "full" else None,
                    )
                    for item in earlier.get(chapter.entity.id, [])
                ],
            )
        )
    return GmContext(
        novel=NovelContext(title=novel.title, world=novel.world, creation=novel.creation),
        characters=list(states.values()),
        current_scene=CurrentSceneContext(
            settings=scene.entity,
            performance=performance(scene),
            prose=scene.text,
            chapter_id=scene.chapter_id,
            chapter_name=novel.chapters[target_index].entity.name,
            present=[reference(key) for key in present(scene)],
            rewind=RewindContext.model_validate(timeline_context(scene)),
        ),
        chapter_scenes=[item.entity for item in novel.scenes if item.chapter_id == scene.chapter_id],
        chapters=chapters,
        discussion=[
            DiscussionMessageContext(
                role=m.role, speaker="用户" if m.role == "user" else "GM", text=m.content
            )
            for m in (scene.messages if discussion is None else discussion)
        ],
    )


def legacy_gm_data(context: GmContext) -> dict:
    """Keep saved shared templates and GM behavior overrides working."""

    def old_scene(scene: SceneContext, *, positions=False, include_prose=True):
        events = []
        for event in scene.performance:
            item = {
                "kind": event.kind,
                "actor_id": event.character.id if event.character else "",
                "text": event.text,
            }
            if positions:
                item.update(id=event.id, position=event.position)
            if event.kind in ("gm_direction", "self_edit"):
                item["audience"] = [character.id for character in event.audience]
            events.append(item)
        data = {"scene": scene.settings.model_dump(mode="json"), "events": events}
        if include_prose:
            data["text"] = scene.prose or ""
        return data

    current = context.current_scene
    audience = [character.id for character in current.present]
    return {
        **context.novel.model_dump(mode="json"),
        "characters": [character.model_dump(mode="json") for character in context.characters],
        "chapter_scenes": [item.model_dump(mode="json") for item in context.chapter_scenes],
        "current_scene": {
            **old_scene(current, positions=True),
            "channel": "character_group",
            "audience": audience,
        },
        "present": audience,
        "present_count": len(audience),
        "timeline": current.rewind.model_dump(mode="json"),
        "chapters": [
            {
                "name": chapter.name,
                "level": chapter.level,
                "content": chapter.summary
                if chapter.level == "summary"
                else [old_scene(scene, include_prose=chapter.level == "full") for scene in chapter.scenes],
            }
            for chapter in context.chapters
        ],
    }
