import asyncio
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_integration import Harness
from test_llm import Input, Sink, tool_message

from ainovel.controller.api import create_app
from ainovel.core.config import Settings
from ainovel.core.domain import (
    CommandSubmission,
    DeleteStoryRequest,
    Direction,
    DirectionRequest,
    Draft,
    GenerateRequest,
    PlanSceneRequest,
    RestoreStoryRequest,
)
from ainovel.core.errors import StoryUnavailableError
from ainovel.llm.client import OpenAIModel
from ainovel.llm.mock import MockModel
from ainovel.llm.schemas import ChatResponse, Choice, DeepSeekChatBody, ProviderUsage


def test_openai_environment_aliases_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "alias-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    settings = Settings(_env_file=None)
    assert settings.llm_api_key.get_secret_value() == "alias-test-key"
    assert settings.llm_base_url == "https://api.deepseek.com/v1"
    assert "alias-test-key" not in repr(settings)


async def test_deepseek_forced_tool_call_thinking_and_cache_token_mapping() -> None:
    sink = Sink()
    model = OpenAIModel(
        Settings(llm_model="deepseek-flash", llm_api_key=SecretStr("test"), _env_file=None), sink
    )
    await model.client.aclose()
    draft = Draft(title="第一幕", text="正文", summary="摘要")

    def respond(request: httpx.Request) -> httpx.Response:
        body = DeepSeekChatBody.model_validate_json(request.content)
        assert body.model == "deepseek-flash"
        assert body.thinking.type == "disabled"
        assert body.tool_choice.function.name == "submit_Draft"
        assert body.tools[0].function.parameters.root == Draft.model_json_schema()
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[Choice(message=tool_message(draft.model_dump_json()), finish_reason="tool_calls")],
                usage=ProviderUsage(prompt_tokens=100, completion_tokens=20, prompt_cache_hit_tokens=70),
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )
    try:
        assert await model.generate(Draft, "writing", Input(instruction="开始"), uuid4()) == draft
        assert sink.traces[0].usage.cached_tokens == 70
    finally:
        await model.close()


@pytest.mark.skipif(
    __import__("os").getenv("AINOVEL_INTEGRATION") != "1", reason="Local database integration"
)
async def test_chapter_decisions_follow_planner_and_user_instruction() -> None:
    harness = Harness()
    await harness.runtime.store.initialize()
    try:
        await harness.create()
        first = await harness.execute(GenerateRequest(instruction="第一幕"))
        assert first.status == "completed", first.error
        await harness.execute(
            DirectionRequest(
                direction=Direction(
                    instruction="本章不要揭露秘密",
                    scope="chapter",
                    chapter_id=(await harness.runtime.store.head(harness.story_id)).story.chapters[0].id,
                )
            )
        )
        second = await harness.execute(GenerateRequest(instruction="继续"))
        second_story = await harness.runtime.store.head(harness.story_id)
        assert len(second_story.story.chapters) == 1
        third = await harness.execute(GenerateRequest(instruction="另起一章，切换到第二天"))
        assert third.status == "completed", third.error
        after = await harness.runtime.store.head(harness.story_id)
        assert len(after.story.chapters) == 2
        assert after.story.scenes[0].chapter_id == after.story.scenes[1].chapter_id
        assert after.story.scenes[2].chapter_id != after.story.scenes[1].chapter_id
        checkpoint = await harness.runtime.store.checkpoint(third.submission.id)
        assert checkpoint.plan.start_new_chapter
        assert "new_chapter" not in second.submission.payload.model_dump_json()
    finally:
        await harness.cleanup()


@pytest.mark.skipif(
    __import__("os").getenv("AINOVEL_INTEGRATION") != "1", reason="Local database integration"
)
async def test_delete_restore_and_all_normal_access_are_consistent() -> None:
    harness = Harness()
    await harness.runtime.store.initialize()
    try:
        await harness.create()
        await harness.execute(GenerateRequest(instruction="第一幕"))
        original = await harness.runtime.store.head(harness.story_id)
        revision_count = len(await harness.runtime.store.revisions(harness.story_id))
        pending = CommandSubmission(
            story_id=harness.story_id,
            base_revision_id=harness.head,
            payload=GenerateRequest(instruction="还未运行"),
        )
        harness.ids.append(pending.id)
        await harness.runtime.store.submit(pending)
        deleted = await harness.execute(DeleteStoryRequest())
        assert deleted.status == "completed", deleted.error
        assert deleted.output.story_deleted
        assert all(story.id != harness.story_id for story in await harness.runtime.store.stories())
        assert any(
            story.id == harness.story_id for story in await harness.runtime.store.stories(deleted=True)
        )
        assert (await harness.runtime.store.command(pending.id)).status == "failed"
        with pytest.raises(StoryUnavailableError):
            await harness.runtime.store.head(harness.story_id)
        with pytest.raises(StoryUnavailableError):
            await harness.runtime.store.revision(original.revision_id)
        with pytest.raises(StoryUnavailableError):
            await harness.runtime.store.revisions(harness.story_id)
        with pytest.raises(StoryUnavailableError):
            await harness.runtime.store.submit(
                CommandSubmission(
                    story_id=harness.story_id,
                    base_revision_id=harness.head,
                    payload=GenerateRequest(instruction="不能写入"),
                )
            )
        await harness.worker.process(pending.id)
        assert (await harness.runtime.store.command(pending.id)).status == "failed"
        app = create_app(Settings(llm_provider="mock"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get(f"/api/stories/{harness.story_id}")).status_code == 410
                assert (await client.get(f"/api/revisions/{original.revision_id}")).status_code == 410
        restored = await harness.execute(RestoreStoryRequest())
        assert restored.status == "completed", restored.error
        current = await harness.runtime.store.head(harness.story_id)
        assert current == original
        assert any(story.id == harness.story_id for story in await harness.runtime.store.stories())
        assert len(await harness.runtime.store.revisions(harness.story_id)) == revision_count
        assert (await harness.runtime.store.command(pending.id)).status == "failed"
    finally:
        await harness.cleanup()


class PausedWriter(MockModel):
    def __init__(self, sink: Sink) -> None:
        super().__init__(sink)
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def choose_agent_tool(self, context, command_id, *, prompt_template=None):
        if context.task == "writing":
            self.started.set()
            await self.resume.wait()
        return await super().choose_agent_tool(context, command_id, prompt_template=prompt_template)


@pytest.mark.skipif(
    __import__("os").getenv("AINOVEL_INTEGRATION") != "1", reason="Local database integration"
)
async def test_inflight_writer_cannot_resurrect_deleted_story() -> None:
    harness = Harness()
    await harness.runtime.store.initialize()
    task = None
    try:
        await harness.create()
        await harness.execute(PlanSceneRequest(instruction="正在生成"))
        from ainovel.service.scene_planning import next_scene

        planned = next_scene((await harness.runtime.store.head(harness.story_id)).story)
        paused = PausedWriter(harness.runtime.sink)
        harness.runtime.services.writing_agent.model = paused
        submission = CommandSubmission(
            story_id=harness.story_id,
            base_revision_id=harness.head,
            payload=GenerateRequest(instruction="正在生成", planned_scene_id=planned.id),
        )
        harness.ids.append(submission.id)
        await harness.runtime.store.submit(submission)
        task = asyncio.create_task(harness.worker.process(submission.id))
        await asyncio.wait_for(paused.started.wait(), timeout=5)
        result = await harness.execute(DeleteStoryRequest())
        assert result.status == "completed", result.error
        paused.resume.set()
        await asyncio.wait_for(task, timeout=5)
        assert (await harness.runtime.store.command(submission.id)).status == "failed"
        with pytest.raises(StoryUnavailableError):
            await harness.runtime.store.head(harness.story_id)
        assert (await harness.execute(RestoreStoryRequest())).status == "completed"
        assert not (await harness.runtime.store.head(harness.story_id)).story.scenes
    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await harness.cleanup()
