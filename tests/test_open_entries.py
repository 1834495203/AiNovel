"""Open author records and compatibility with persisted pre-migration scene patches."""

from uuid import uuid4

from ainovel.core.characters import CharacterState, Profile, StatePatch
from ainovel.core.records import CollectionPatch, Entry, WorldPatch, WorldSetting


def test_arbitrary_nested_records_survive_edit_and_round_trip() -> None:
    entry = Entry(name="雨为什么往上落", children=[Entry(name="例外", value="钟楼附近")])
    world = WorldSetting(entries=[entry])
    changed = entry.model_copy(deep=True)
    changed.children[0].value = "钟声响起时"
    world = WorldPatch(entries=CollectionPatch[Entry](upsert=[changed])).apply(world)
    restored = WorldSetting.model_validate_json(world.model_dump_json())
    assert restored.entries == [changed]
    assert "钟声响起时" in restored.entries[0].text()
    assert WorldPatch(entries=CollectionPatch[Entry](remove=[entry.id])).apply(restored).entries == []
    assert set(WorldSetting.model_fields) == {"entries"}
    assert set(Profile.model_fields) == set(CharacterState.model_fields) == {"entries"}


def test_legacy_deltas_replace_scalar_state_and_remove_the_original_record() -> None:
    identifier = uuid4()
    original = CharacterState.model_validate(
        {"emotion": "疑惑", "thoughts": ["寻找出口"], "knowledge": [{"id": identifier, "name": "入口"}]}
    )
    updated = StatePatch.model_validate(
        {"emotion": "安心", "thoughts": [], "knowledge": {"remove": [identifier]}}
    ).apply(original)
    assert len(updated.entries) == 1
    assert updated.entries[0].value == "安心"
    assert updated.entries[0].id == original.entries[0].id
    assert original.entries[0].value == "疑惑"
    assert CharacterState.model_validate_json(original.model_dump_json()) == original


def test_legacy_world_patch_preserves_nested_details_and_existing_identity() -> None:
    identifier = uuid4()
    original = WorldSetting.model_validate({"places": [{"id": identifier, "name": "旧港"}]})
    patch = WorldPatch.model_validate(
        {
            "places": {
                "upsert": [
                    {"id": identifier, "name": "新港", "custom_fields": [{"name": "水深", "value": 8}]}
                ]
            }
        }
    )
    updated = patch.apply(original)
    assert len(updated.entries) == 1
    assert updated.entries[0].id == original.entries[0].id
    assert updated.entries[0].children[0].value == 8
    assert WorldPatch.model_validate({"places": {"remove": [identifier]}}).apply(updated).entries == []
