"""Discussion tools stage settings changes; prose is never an editable argument."""

from uuid import UUID

from ainovel.core.characters import Alias, CharacterPatch
from ainovel.core.discussion import (
    DiscussionCharacter,
    DiscussionExcerpt,
    DiscussionFields,
    DiscussionStory,
    UpdateSettingsArguments,
)
from ainovel.core.domain import Story
from ainovel.core.fields import FieldTarget
from ainovel.core.records import merge_records


def context_story(story: Story, revision_id: UUID, recent_scenes: int) -> DiscussionStory:
    return DiscussionStory(
        title=story.title,
        revision_id=revision_id,
        world=story.world,
        preferences=story.preferences,
        writer=story.writer,
        custom_fields=story.custom_fields,
        characters=[
            DiscussionCharacter(id=character.id, name=character.name, snapshot=story.snapshot(character.id))
            for character in story.characters
        ],
        recent_scenes=[
            DiscussionExcerpt(title=scene.title, text=scene.text, summary=scene.summary)
            for scene in story.scenes[-recent_scenes:]
        ],
        field_documents=[DiscussionFields(target=FieldTarget(scope=scope, entity_id=item.id), name=item.title, entries=item.custom_fields)
            for scope, items in (("chapter", story.chapters), ("scene", story.scenes)) for item in items],
    )


def apply_settings(story: Story, arguments: UpdateSettingsArguments) -> Story:
    result = story.model_copy(deep=True)
    result.apply_metadata(arguments.patch)
    ids = [change.character_id for change in arguments.characters]
    if len(ids) != len(set(ids)):
        raise ValueError("同一次工具调用不能重复修改同一个角色")
    for change in arguments.characters:
        character = next((item for item in result.characters if item.id == change.character_id), None)
        if character is None:
            raise ValueError("只能修改已有角色，新增角色需要创作审批")
        snapshot = result.snapshot(character.id)
        aliases = change.aliases.model_copy(deep=True)
        if change.name is not None and change.name != character.name:
            if any(item.id != character.id and item.name == change.name for item in result.characters):
                raise ValueError("角色名称已被使用")
            if not any(item.name == character.name for item in snapshot.aliases + aliases.upsert):
                aliases.upsert.append(
                    Alias(name=character.name, reason="改名后保留旧称，供正文识别", description=change.reason)
                )
            character.name = change.name
        if result.scenes:
            result.scenes[-1].character_patches.append(
                CharacterPatch(
                    character_id=character.id,
                    scene_id=result.scenes[-1].id,
                    profile=change.profile,
                    state=change.state,
                    aliases=aliases,
                    custom_fields=change.custom_fields,
                    reason=change.reason,
                )
            )
        else:
            baseline = next(item for item in result.initial_snapshots if item.character_id == character.id)
            baseline.profile = change.profile.apply(snapshot.profile)
            baseline.state = change.state.apply(snapshot.state)
            baseline.aliases = merge_records(snapshot.aliases, aliases)
            baseline.custom_fields = merge_records(snapshot.custom_fields, change.custom_fields)
    if result.chapters != story.chapters or any(
        (new.id, new.chapter_id, new.title, new.text, new.summary, new.source_command_id)
        != (old.id, old.chapter_id, old.title, old.text, old.summary, old.source_command_id)
        for new, old in zip(result.scenes, story.scenes, strict=True)
    ):
        raise ValueError("讨论工具禁止修改章节和正文")
    return result
