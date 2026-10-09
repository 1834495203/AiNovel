"""Explicit opt-in smoke test against the user's configured model; synthetic story data only."""

import os

import pytest
from test_integration import Harness

from ainovel.core.config import Settings
from ainovel.core.domain import GenerateRequest, InterviewRequest, Turn

pytestmark = pytest.mark.skipif(
    os.getenv("AINOVEL_LIVE_LLM") != "1", reason="Set AINOVEL_LIVE_LLM=1 to call the configured paid model"
)


async def test_live_deepseek_interview_and_scene_commit() -> None:
    settings = Settings()
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "deepseek-flash"
    harness = Harness(settings)
    await harness.runtime.store.initialize()
    try:
        interview = await harness.execute(
            InterviewRequest(
                turns=[
                    Turn(
                        role="user",
                        content=(
                            "小说名《雨夜来信》，近未来都市。主角林初是一名谨慎而好奇的调查员，"
                            "擅长观察细节，说话简洁。喜欢克制慢热的悬疑，不喜欢角色降智和突然反转。"
                            "这些是全部初始需求，请整理设定供我确认，不再追问细节。"
                        ),
                    )
                ]
            )
        )
        assert interview.status == "completed", interview.error
        assert interview.output.interview is not None
        assert interview.output.interview.reply
        interview_events = await harness.runtime.store.events(interview.submission.id)
        assert any(
            item.event.trace and item.event.trace.tool_name == "submit_InterviewResult"
            for item in interview_events
        )
        created = await harness.create()
        assert created.status == "completed", created.error
        generated = await harness.execute(
            GenerateRequest(
                instruction=(
                    "写第一幕，林初独自在屋里收到一封日期写着明天的来信。"
                    "只出现林初，不需要新人物。用动作表现犹疑，约500到800字，情节保持克制。"
                )
            )
        )
        assert generated.status == "completed", generated.error
        story = await harness.runtime.store.head(harness.story_id)
        assert len(story.story.scenes) == 1 and len(story.story.chapters) == 1
        assert len(story.story.scenes[0].text) >= 100
        assert story.story.scenes[0].character_patches
        events = await harness.runtime.store.events(generated.submission.id)
        calls = [item.event.trace for item in events if item.event.type == "llm.completed"]
        assert calls and all(call.model == "deepseek-flash" for call in calls)
        assert any(call.usage.input_tokens is not None for call in calls)
    finally:
        await harness.cleanup()
