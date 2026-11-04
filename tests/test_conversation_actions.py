
from uuid import uuid4

import pytest
from configured_fields import make_world

from ainovel.core.config import Settings
from ainovel.core.discussion import DiscussionRequest, DiscussionResult
from ainovel.core.domain import (
    Chapter,
    CommandOutput,
    CommandSubmission,
    CreateStoryRequest,
    CreationProposal,
    Scene,
    Story,
    UndoRequest,
)
from ainovel.core.fields import FieldTarget
from ainovel.core.records import Entry
from ainovel.mapper.models import CommandRow
from ainovel.mapper.store import Store
from ainovel.service.fields import write_fields
from ainovel.service.undo import UndoService


async def test_readonly_conversation_undo_preserves_other_sessions_and_newer_story_changes():
    store = Store(Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:"))
    await store.initialize()
    async def start(submission):
        await store.submit(submission)
        async with store.sessions.begin() as session:
            row = await session.get(CommandRow, submission.id)
            row.status = "running"
    try:
        create = CommandSubmission(payload=CreateStoryRequest(confirmed=True, proposal=CreationProposal(title="测试", world=make_world('城市'))))
        await start(create)
        story = Story(title="测试", world=make_world('城市'))
        first = await store.commit(create, story, "创建", [], [])
        chats = []
        for session_id in (uuid4(), uuid4()):
            command = CommandSubmission(story_id=story.id, base_revision_id=first.revision_id,
                payload=DiscussionRequest(session_id=session_id, message="讨论"))
            await start(command)
            await store.finish(command.id, CommandOutput(story_id=story.id, discussion=DiscussionResult(
                session_id=session_id, reply="建议", read_revision_id=first.revision_id)))
            chats.append(command)
        # A later writing operation changes the canonical head.
        from ainovel.core.domain import UpdateStoryRequest
        from ainovel.core.records import StoryMetadataPatch
        change = CommandSubmission(story_id=story.id, base_revision_id=first.revision_id,
            payload=UpdateStoryRequest(patch=StoryMetadataPatch(title="新的书名")))
        await start(change)
        story.title = "新的书名"
        await store.commit(change, story, "写作修改", [], [])
        before = await store.head(story.id)
        scope = chats[0].payload.session_id
        state = await UndoService(store).state(scope)
        undo = CommandSubmission(story_id=story.id, base_revision_id=before.revision_id,
            payload=UndoRequest(scope_id=scope, target_command_id=chats[0].id, expected_operation_id=state.operation_id))
        await start(undo)
        await UndoService(store).execute(undo, undo.payload)
        assert await store.head(story.id) == before
        assert (await store.command(chats[0].id)).undone
        assert not (await store.command(chats[1].id)).undone
        assert (await UndoService(store).state(story.id)).command_id == change.id
    finally:
        await store.close()


def test_committed_scene_and_chapter_fields_are_locked():
    chapter = Chapter(title="章")
    scene = Scene(chapter_id=chapter.id, title="幕", text="已确认正文", summary="摘要")
    story = Story(title="书", chapters=[chapter], scenes=[scene])
    for target in (FieldTarget(scope="scene", entity_id=scene.id), FieldTarget(scope="chapter", entity_id=chapter.id)):
        with pytest.raises(ValueError, match="设定已锁定"):
            write_fields(story, target, [Entry(name="修改设定")], "测试")
    assert scene.custom_fields == [] and chapter.custom_fields == []




async def test_readonly_discussion_can_finish_when_writing_advances(monkeypatch):
    from types import SimpleNamespace

    from ainovel.core.discussion import ReplyAction
    from ainovel.core.domain import StoryView
    from ainovel.service import discussion

    old = StoryView(revision_id=uuid4(), story=Story(title="原始状态"))
    current = old
    async def head(*args):
        return current
    async def history(*args):
        return []
    async def template(*args):
        return None
    async def choose(*args, **kwargs):
        nonlocal current
        current = old.model_copy(update={"revision_id": uuid4(), "story": old.story.model_copy(update={"title": "写幕的新状态"})})
        return ReplyAction(reply="基于本轮读取的状态给出建议")
    monkeypatch.setattr(discussion.DiscussionRepository, "history", history)
    monkeypatch.setattr(discussion, "resolve_command_template", template)
    services = SimpleNamespace(store=SimpleNamespace(head=head), model=SimpleNamespace(choose_agent_tool=choose),
        settings=Settings(_env_file=None))
    request = DiscussionRequest(session_id=uuid4(), message="讨论后续")
    result = await discussion.DiscussionHandler(services).turn(CommandSubmission(
        story_id=old.story.id, base_revision_id=old.revision_id, payload=request), request)
    assert result.discussion.read_revision_id == old.revision_id
    assert not result.discussion.settings_changed
    assert current.story.title == "写幕的新状态"
