"""Typed discussion messages, tool contracts and current-story context."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from ainovel.core.base import Model
from ainovel.core.characters import Alias, ProfilePatch, Snapshot, StatePatch
from ainovel.core.records import (
    CollectionPatch,
    CustomField,
    ReaderPreferences,
    StoryMetadataPatch,
    WorldSetting,
    WriterWorkspace,
)
from ainovel.utils.time import now


class StartDiscussionRequest(Model):
    kind: Literal["start_discussion"] = "start_discussion"
    session_id: UUID = Field(default_factory=uuid4)
    title: str = Field(default="新讨论", min_length=1, max_length=100)


class DiscussionRequest(Model):
    kind: Literal["discussion"] = "discussion"
    session_id: UUID
    message: str = Field(min_length=1, max_length=12000)


class SearchStoryArguments(Model):
    query: str = Field(min_length=1, max_length=2000)


class CharacterChange(Model):
    character_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=80)
    profile: ProfilePatch = Field(default_factory=ProfilePatch)
    state: StatePatch = Field(default_factory=StatePatch)
    aliases: CollectionPatch[Alias] = Field(default_factory=CollectionPatch[Alias])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    reason: str = Field(min_length=1)


class UpdateSettingsArguments(Model):
    patch: StoryMetadataPatch = Field(default_factory=StoryMetadataPatch)
    characters: list[CharacterChange] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)


class SearchStoryAction(Model):
    kind: Literal["search_story"] = "search_story"
    arguments: SearchStoryArguments


class UpdateSettingsAction(Model):
    kind: Literal["update_story_settings"] = "update_story_settings"
    arguments: UpdateSettingsArguments


class ReplyAction(Model):
    kind: Literal["reply"] = "reply"
    reply: str = Field(min_length=1, max_length=30000)


DiscussionAction = Annotated[
    SearchStoryAction | UpdateSettingsAction | ReplyAction, Field(discriminator="kind")
]


class DiscussionExcerpt(Model):
    title: str
    text: str
    summary: str = ""


class DiscussionCharacter(Model):
    id: UUID
    name: str
    snapshot: Snapshot


class DiscussionStory(Model):
    title: str
    revision_id: UUID
    world: WorldSetting
    preferences: ReaderPreferences
    writer: WriterWorkspace
    characters: list[DiscussionCharacter]
    custom_fields: list[CustomField]
    recent_scenes: list[DiscussionExcerpt]


class DiscussionTurn(Model):
    role: Literal["user", "assistant"]
    content: str


class DiscussionToolResult(Model):
    name: Literal["search_story", "update_story_settings"]
    success: bool = True
    message: str
    excerpts: list[DiscussionExcerpt] = Field(default_factory=list)


class DiscussionContext(Model):
    story: DiscussionStory
    history: list[DiscussionTurn]
    message: str
    tool_results: list[DiscussionToolResult] = Field(default_factory=list)


class DiscussionResult(Model):
    session_id: UUID
    reply: str
    read_revision_id: UUID
    settings_changed: bool = False
    updated_revision_id: UUID | None = None
    tool_results: list[DiscussionToolResult] = Field(default_factory=list)


class DiscussionSession(Model):
    id: UUID
    story_id: UUID
    title: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    message_count: int = 0
