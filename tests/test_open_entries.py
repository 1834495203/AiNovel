"""Open author records and compatibility with persisted pre-migration scene patches."""


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


def test_old_state_fields_are_rejected():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CharacterState.model_validate({"emotion":"疑惑"})
    with pytest.raises(ValidationError):
        StatePatch.model_validate({"emotion":"安心"})


def test_old_world_fields_are_rejected():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        WorldSetting.model_validate({"places":[{"name":"旧港"}]})
    with pytest.raises(ValidationError):
        WorldPatch.model_validate({"places":{"upsert":[]}})
