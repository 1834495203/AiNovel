"""Character identities, evolving profiles and replayable scene deltas."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from ainovel.core.base import Model
from ainovel.core.legacy import PROFILE_FIELDS, STATE_FIELDS, migrate_entries
from ainovel.core.records import CollectionPatch, CustomField, Entry, Record, merge_records


class Alias(Record):
    used_by_character_id: UUID | None = None


class Profile(Model):
    entries: list[Entry] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def legacy_profile(cls, value: object) -> object:
        return migrate_entries(value, PROFILE_FIELDS, "profile")


class ProfilePatch(Model):
    entries: CollectionPatch[Entry] = Field(default_factory=CollectionPatch[Entry])

    @model_validator(mode="before")
    @classmethod
    def legacy_patch(cls, value: object) -> object:
        return migrate_entries(value, PROFILE_FIELDS, "profile", patch=True)

    def apply(self, profile: Profile) -> Profile:
        result = profile.model_copy(deep=True)
        result.entries = merge_records(result.entries, self.entries)
        return result


class CharacterSpec(Model):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[Alias] = Field(default_factory=list)
    profile: Profile = Field(default_factory=Profile)
    custom_fields: list[CustomField] = Field(default_factory=list)


class Character(CharacterSpec):
    id: UUID = Field(default_factory=uuid4)
    introduced_scene_id: UUID | None = None


class Position(Model):
    """Legacy checkpoint type; current state uses open entries."""

    topic: str
    internal_belief: str
    expressed_belief: str
    intended_action: str
    actual_action: str = ""


class Experience(Model):
    id: UUID = Field(default_factory=uuid4)
    scene_id: UUID
    event: str
    keywords: list[str] = Field(default_factory=list)
    entries: list[Entry] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def legacy_experience(cls, value: object) -> object:
        from ainovel.core.legacy import LegacyField

        return migrate_entries(
            value,
            (
                LegacyField(name="interpretation", label="当时的理解", kind="scalar"),
                LegacyField(name="emotional_impact", label="当时的感受", kind="scalar"),
                LegacyField(name="custom_fields", label=""),
            ),
            "experience",
        )


class CharacterGoal(Record):
    """Legacy checkpoint type; current state uses open entries."""

    status: Literal["active", "achieved", "abandoned"] = "active"
    target_character_id: UUID | None = None


class CharacterState(Model):
    entries: list[Entry] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def legacy_state(cls, value: object) -> object:
        return migrate_entries(value, STATE_FIELDS, "state")


class StatePatch(Model):
    entries: CollectionPatch[Entry] = Field(default_factory=CollectionPatch[Entry])

    @model_validator(mode="before")
    @classmethod
    def legacy_patch(cls, value: object) -> object:
        return migrate_entries(value, STATE_FIELDS, "state", patch=True)

    def apply(self, state: CharacterState) -> CharacterState:
        result = state.model_copy(deep=True)
        result.entries = merge_records(result.entries, self.entries)
        return result

    @classmethod
    def between(cls, old: CharacterState, new: CharacterState) -> "StatePatch":
        return cls(
            entries=CollectionPatch[Entry](
                upsert=[item for item in new.entries if item not in old.entries],
                remove=[
                    item.id
                    for item in old.entries
                    if not any(candidate.id == item.id for candidate in new.entries)
                ],
            )
        )


class CharacterPatch(Model):
    id: UUID = Field(default_factory=uuid4)
    character_id: UUID
    scene_id: UUID
    state: StatePatch = Field(default_factory=StatePatch)
    profile: ProfilePatch = Field(default_factory=ProfilePatch)
    aliases: CollectionPatch[Alias] = Field(default_factory=CollectionPatch[Alias])
    custom_fields: CollectionPatch[CustomField] = Field(default_factory=CollectionPatch[CustomField])
    new_experiences: list[Experience] = Field(default_factory=list)
    reason: str = ""


class Snapshot(Model):
    """Materialized API projection; only initial baselines and scene patches are persisted."""

    id: UUID = Field(default_factory=uuid4)
    character_id: UUID
    scene_id: UUID | None = None
    state: CharacterState = Field(default_factory=CharacterState)
    experiences: list[Experience] = Field(default_factory=list)
    profile: Profile = Field(default_factory=Profile)
    aliases: list[Alias] = Field(default_factory=list)
    custom_fields: list[CustomField] = Field(default_factory=list)

    def apply(self, patch: CharacterPatch) -> "Snapshot":
        if patch.character_id != self.character_id:
            raise ValueError("角色增量不属于当前角色")
        return Snapshot(
            id=patch.id,
            character_id=self.character_id,
            scene_id=patch.scene_id,
            state=patch.state.apply(self.state),
            profile=patch.profile.apply(self.profile),
            aliases=merge_records(self.aliases, patch.aliases),
            custom_fields=merge_records(self.custom_fields, patch.custom_fields),
            experiences=[*self.experiences, *patch.new_experiences],
        )


class SnapshotContext(Model):
    id: UUID
    character_id: UUID
    scene_id: UUID | None
    state: CharacterState

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> "SnapshotContext":
        return cls(
            id=snapshot.id,
            character_id=snapshot.character_id,
            scene_id=snapshot.scene_id,
            state=snapshot.state,
        )
