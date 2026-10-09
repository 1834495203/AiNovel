from uuid import uuid4

import pytest
from pydantic import ValidationError

from ainovel.core.characters import Character, Snapshot
from ainovel.core.domain import (
    Chapter,
    CommandSubmission,
    CreateStoryRequest,
    Direction,
    GenerateRequest,
    JudgeResult,
    Scene,
    Story,
    StorySetup,
)
from ainovel.utils.chunking import ParagraphChunker


def test_chunker_never_cuts_paragraph_and_only_context_sentences_overlap() -> None:
    chapter, scene = uuid4(), uuid4()
    paragraphs = ["一" * 90 + "。尾句。", "第二段。" * 50, "第三段。最后一句！"]
    chunks = ParagraphChunker(100).split("\n\n".join(paragraphs), chapter, scene)
    assert [chunk.content for chunk in chunks] == paragraphs
    assert chunks[0].next_context == "第二段。"
    assert chunks[1].previous_context == "尾句。"
    assert chunks[2].previous_context == "第二段。"
    assert "".join(chunk.content for chunk in chunks) == "".join(paragraphs)


def test_submission_requires_explicit_revision_and_confirmation() -> None:
    with pytest.raises(ValidationError):
        CommandSubmission(payload=GenerateRequest(instruction="继续"))
    with pytest.raises(ValidationError):
        CreateStoryRequest(setup=StorySetup(title="小说", world="城市"), confirmed=False)
    with pytest.raises(ValidationError):
        Direction(instruction="这里少一点雨", scope="chapter")


def test_hard_failure_cannot_be_overridden_by_should_rewrite_false() -> None:
    from ainovel.core.domain import Issue

    judge = JudgeResult(
        user_intent_satisfied=True,
        direction_satisfied=True,
        overall_score=9,
        should_rewrite=False,
        rewrite_instructions=[],
        issues=[Issue(category="direction", severity="hard", description="违反不要接吻")],
    )
    assert judge.blocked


def test_historical_snapshot_does_not_see_future_character() -> None:
    chapter = Chapter(title="第一章")
    first = Scene(chapter_id=chapter.id, title="一", text="过去", summary="过去")
    second = Scene(chapter_id=chapter.id, title="二", text="后来", summary="后来")
    npc = Character(name="后来的人", introduced_scene_id=second.id)
    story = Story(
        setup=StorySetup(title="小说", world="城市"),
        chapters=[chapter],
        scenes=[first, second],
        characters=[npc],
        initial_snapshots=[Snapshot(character_id=npc.id)],
    )
    with pytest.raises(ValueError, match="尚未出现"):
        story.snapshot(npc.id, first.id)
    assert story.snapshot(npc.id, second.id).character_id == npc.id
