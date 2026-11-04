"""Erasure tests use isolated synthetic resources, including real PG when opted in."""

import os
from uuid import uuid4

import pytest
from configured_fields import make_world
from sqlalchemy import delete, func, select, text

from ainovel.core.config import Settings
from ainovel.core.domain import (
    CommandOutput,
    CommandSubmission,
    ContextChunk,
    CreateStoryRequest,
    CreationProposal,
    Event,
    InterviewRequest,
    PromptTemplateVersion,
    SaveSetupDraftRequest,
    SetSetupDraftDeletedRequest,
    SetupDraft,
    Story,
    UndoRequest,
)
from ainovel.core.errors import ConflictError
from ainovel.mapper.drafts import DraftRepository
from ainovel.mapper.lifecycle import purge
from ainovel.mapper.models import (
    ChunkRow,
    CommandRow,
    EventRow,
    FieldChangeRow,
    FieldSnapshotRow,
    OperationRow,
    PromptTemplateBindingRow,
    PromptTemplateVersionRow,
    ResourceDeletionRow,
    RevisionRow,
    SetupDraftRow,
    StoryRow,
)
from ainovel.mapper.store import Store
from ainovel.service.undo import UndoService
from ainovel.utils.time import now


class Resources:
    def __init__(self, store):
        self.store = store
        self.ids = set()
        self.commands = set()
        self.stories = set()

    def scope(self):
        value = uuid4()
        self.ids.add(value)
        return value

    async def start(self, payload, session_id=None):
        submission = CommandSubmission(payload=payload, session_id=session_id)
        self.commands.add(submission.id)
        await self.store.submit(submission)
        async with self.store.sessions.begin() as session:
            row = await session.get(CommandRow, submission.id)
            row.status = "running"
        return submission

    async def draft(self):
        draft_id = self.scope()
        interview = await self.start(InterviewRequest(turns=[]), draft_id)
        await self.store.finish(interview.id, CommandOutput(message="合成访谈"))
        saved = await self.start(SaveSetupDraftRequest(draft=SetupDraft(id=draft_id, title="待删除构思")), draft_id)
        await DraftRepository(self.store).save(saved, saved.payload)
        return draft_id, interview, saved

    async def trash_draft(self, draft_id, deleted=True):
        command = await self.start(SetSetupDraftDeletedRequest(deleted=deleted), draft_id)
        await DraftRepository(self.store).set_deleted(command, deleted)
        return command

    async def create(self, draft_id=None):
        command = await self.start(CreateStoryRequest(
            confirmed=True, proposal=CreationProposal(title="合成删除测试", world=make_world('城市')),
        ), draft_id)
        story = Story(title="合成删除测试", world=make_world('城市'))
        self.stories.add(story.id)
        self.ids.add(story.id)
        chunk = ContextChunk(chapter_id=uuid4(), ordinal=0, kind="paragraph", content="合成正文")
        output = await self.store.commit(command, story, "创建", [chunk], [[0.0] * 8])
        return story.id, output.revision_id, command

    async def cleanup(self):
        async with self.store.sessions.begin() as session:
            await session.execute(delete(ChunkRow).where(ChunkRow.revision_id.in_(
                select(RevisionRow.id).where(RevisionRow.story_id.in_(self.stories))
            )))
            for model in (FieldChangeRow, FieldSnapshotRow, RevisionRow):
                await session.execute(delete(model).where(model.story_id.in_(self.stories)))
            await session.execute(delete(StoryRow).where(StoryRow.id.in_(self.stories)))
            for model in (PromptTemplateBindingRow, PromptTemplateVersionRow):
                await session.execute(delete(model).where(model.scope_id.in_(self.ids)))
            await session.execute(delete(SetupDraftRow).where(SetupDraftRow.id.in_(self.ids)))
            await session.execute(delete(OperationRow).where(OperationRow.command_id.in_(self.commands)))
            await session.execute(delete(EventRow).where(EventRow.command_id.in_(self.commands)))
            await session.execute(delete(CommandRow).where(CommandRow.id.in_(self.commands)))
            await session.execute(delete(ResourceDeletionRow).where(ResourceDeletionRow.id.in_(self.ids | self.commands)))


@pytest.fixture(params=["sqlite", "postgres"])
async def resources(request):
    if request.param == "postgres" and os.getenv("AINOVEL_INTEGRATION") != "1":
        pytest.skip("Opt-in PostgreSQL lifecycle checks")
    settings = (Settings(llm_provider="mock") if request.param == "postgres" else
                Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:"))
    store = Store(settings)
    await store.initialize()
    harness = Resources(store)
    try:
        yield harness
    finally:
        await harness.cleanup()
        await store.close()


async def test_draft_trash_restore_undo_and_late_autosave(resources):
    h = resources
    repository = DraftRepository(h.store)
    draft_id, _, _ = await h.draft()
    late = await h.start(SaveSetupDraftRequest(draft=SetupDraft(id=draft_id, input_text="迟到保存")), draft_id)
    await h.trash_draft(draft_id)
    assert draft_id not in {item.id for item in await repository.list()}
    assert draft_id in {item.id for item in await repository.list(deleted=True)}
    assert (await h.store.command(late.id)).status == "failed"
    with pytest.raises(ConflictError):
        await repository.save(late, late.payload)
    with pytest.raises(ConflictError):
        await h.store.submit(CommandSubmission(session_id=draft_id, payload=InterviewRequest(turns=[])))
    await h.trash_draft(draft_id, False)
    assert (await repository.get(draft_id)).input_text == ""
    assert (await h.store.command(late.id)).status == "failed"
    # Undoing restoration returns it to the trash; undoing deletion restores it.
    for deleted in (True, False):
        state = await UndoService(h.store).state(draft_id)
        undo = await h.start(UndoRequest(expected_operation_id=state.operation_id), draft_id)
        await UndoService(h.store).execute(undo, undo.payload)
        assert draft_id in {item.id for item in await repository.list(deleted=deleted)}


async def test_draft_purge_erases_history_and_rejects_replay_without_touching_other_drafts(resources):
    h = resources
    draft_id, interview, saved = await h.draft()
    other_id, _, _ = await h.draft()
    with pytest.raises(ConflictError):
        await purge(h.store, draft_id, draft=True)
    await h.trash_draft(draft_id)
    erased = set((await purge(h.store, draft_id, draft=True)).command_ids)
    assert {interview.id, saved.id} <= erased
    assert other_id in {item.id for item in await DraftRepository(h.store).list()}
    assert draft_id not in {item.id for item in await DraftRepository(h.store).list(deleted=True)}
    assert await h.store.session_commands(draft_id) == []
    async with h.store.sessions() as session:
        for model in (CommandRow, EventRow, OperationRow):
            key = model.id if model is CommandRow else model.command_id
            assert await session.scalar(select(func.count()).select_from(model).where(key.in_(erased))) == 0
    for submission in (saved, CommandSubmission(session_id=draft_id, payload=InterviewRequest(turns=[]))):
        with pytest.raises(ConflictError):
            await h.store.submit(submission)
    # A delayed event from a stale worker cannot recreate erased history.
    await h.store.append_event(Event(command_id=interview.id, type="llm.completed"))
    assert await h.store.events(interview.id) == []
    assert (await purge(h.store, draft_id, draft=True)).command_ids == []


async def test_story_purge_also_erases_creation_draft_and_keeps_unrelated_story(resources):
    h = resources
    draft_id, interview, saved = await h.draft()
    story_id, revision_id, create = await h.create(draft_id)
    other_id, _, _ = await h.create()
    with pytest.raises(ConflictError):
        await purge(h.store, story_id, draft=False)
    async with h.store.sessions.begin() as session:
        for scope_id in (story_id, other_id):
            version = PromptTemplateVersion(id=uuid4(), scope="story", scope_id=scope_id,
                                            task="writing", name="合成作品提示词", version=1, source="合成源码")
            session.add(PromptTemplateVersionRow(id=version.id, scope_id=scope_id, task="writing", version=1, payload=version))
            session.add(PromptTemplateBindingRow(scope_id=scope_id, task="writing", version_id=version.id))
        story = await session.get(StoryRow, story_id)
        story.deleted_at = now()
        # Erasure must not need to deserialize a broken old checkpoint.
        await session.execute(text("UPDATE commands SET checkpoint = :raw WHERE id = :id"),
                              {"raw": '{"old_format":true}', "id": create.id if h.store.engine.dialect.name == "postgresql" else create.id.hex})
    result = await purge(h.store, story_id, draft=False)
    erased = set(result.command_ids)
    assert {draft_id, story_id, revision_id} <= set(result.cache_ids)
    assert {interview.id, saved.id, create.id} <= erased
    assert (await h.store.head(other_id)).story.id == other_id
    async with h.store.sessions() as session:
        assert await session.get(StoryRow, story_id) is None
        assert await session.get(RevisionRow, revision_id) is None
        assert await session.get(SetupDraftRow, draft_id) is None
        assert await session.get(FieldChangeRow, revision_id) is None
        assert await session.get(FieldSnapshotRow, revision_id) is None
        for model in (PromptTemplateBindingRow, PromptTemplateVersionRow):
            assert await session.scalar(select(func.count()).select_from(model).where(model.scope_id == story_id)) == 0
            assert await session.scalar(select(func.count()).select_from(model).where(model.scope_id == other_id)) == 1
        assert await session.scalar(select(func.count()).select_from(ChunkRow).where(ChunkRow.revision_id == revision_id)) == 0
        assert await session.scalar(select(func.count()).select_from(OperationRow).where(OperationRow.scope_id.in_([draft_id, story_id]))) == 0
    assert await h.store.session_commands(draft_id) == []
    with pytest.raises(ConflictError):
        await h.store.submit(create)




async def test_delete_api_requires_trash_and_removes_queue_ids(resources, monkeypatch):
    from types import SimpleNamespace

    import httpx

    from ainovel.controller import api

    h = resources
    draft_id, interview, _ = await h.draft()

    class Queue:
        removed = []

        async def remove(self, command_ids):
            self.removed.extend(command_ids)

    queue = Queue()
    monkeypatch.setattr(api, "Runtime", lambda settings: SimpleNamespace(store=h.store, settings=Settings(_env_file=None)))
    app = api.create_app(job_queue=queue)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.delete(f"/api/drafts/{draft_id}")
        assert response.status_code == 409
        await h.trash_draft(draft_id)
        assert str(draft_id) in {item["id"] for item in (await client.get("/api/drafts?deleted=true")).json()}
        response = await client.delete(f"/api/drafts/{draft_id}")
        assert response.status_code == 200
        assert str(draft_id) in response.json()["cache_ids"]
        assert interview.id in queue.removed
        assert str(draft_id) not in {item["id"] for item in (await client.get("/api/drafts?deleted=true")).json()}
        assert (await client.get(f"/api/drafts/{draft_id}")).status_code == 400
        assert (await client.delete(f"/api/drafts/{draft_id}")).status_code == 200
