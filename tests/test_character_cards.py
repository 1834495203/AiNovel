"""Cards must replay the selected stage and hide characters not yet introduced."""

from uuid import UUID, uuid4

import httpx

from ainovel.controller.api import create_app
from ainovel.core.characters import Character, CharacterPatch, Profile, Snapshot, StatePatch
from ainovel.core.config import Settings
from ainovel.core.domain import Chapter, Revision, Scene, Story
from ainovel.core.records import CollectionPatch, Entry
from ainovel.mapper.store import Store


async def test_character_cards_replay_history_and_preserve_complete_entries(monkeypatch) -> None:
    chapter = Chapter(title="第一章")
    first = Scene(chapter_id=chapter.id, title="初遇", text="林初等待。", summary="初遇")
    second = Scene(chapter_id=chapter.id, title="来信", text="林初读信。", summary="来信")
    character = Character(name="林初", profile=Profile(entries=[Entry(name="背景", value="学生")]))
    newcomer = Character(name="信使", introduced_scene_id=second.id)
    entry = Entry(name="心理", value="不安", reason="收到来信", source_scene_id=second.id)
    second.character_patches = [
        CharacterPatch(
            character_id=character.id,
            scene_id=second.id,
            state=StatePatch(entries=CollectionPatch[Entry](upsert=[entry])),
        )
    ]
    story = Story(
        title="来信",
        characters=[character, newcomer],
        chapters=[chapter],
        scenes=[first, second],
        initial_snapshots=[
            Snapshot(character_id=character.id, profile=character.profile),
            Snapshot(character_id=newcomer.id),
        ],
    )
    revision = Revision(story_id=story.id, command_id=uuid4(), reason="测试", story=story)

    async def read_revision(self: Store, revision_id: UUID) -> Revision:
        assert revision_id == revision.id
        return revision

    monkeypatch.setattr(Store, "revision", read_revision)
    app = create_app(Settings(_env_file=None, llm_provider="mock"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/revisions/{revision.id}/characters"
        earlier = await client.get(path, params={"scene_id": str(first.id)})
        assert earlier.status_code == 200
        assert len(earlier.json()) == 1
        assert earlier.json()[0]["state"]["entries"] == []
        assert earlier.json()[0]["profile"]["entries"][0]["value"] == "学生"
        latest = await client.get(path)
        assert len(latest.json()) == 2
        assert latest.json()[0]["state"]["entries"][0] == entry.model_dump(mode="json")
        assert (await client.get(path, params={"scene_id": str(uuid4())})).status_code == 404
    assert story.snapshot(character.id, first.id).state.entries == []
