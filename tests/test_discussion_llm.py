from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from test_llm import Sink

from ainovel.core.config import Settings
from ainovel.core.discussion import (
    DiscussionContext,
    DiscussionStory,
    DiscussionTurn,
    ReplyAction,
    SearchStoryAction,
    UpdateSettingsAction,
    UpdateSettingsArguments,
)
from ainovel.core.records import ReaderPreferences, WorldSetting, WriterWorkspace
from ainovel.llm.client import OpenAIModel
from ainovel.llm.schemas import (
    ChatResponse,
    Choice,
    DeepSeekChatBody,
    FunctionCall,
    ProviderMessage,
    ToolCall,
)


def context() -> DiscussionContext:
    return DiscussionContext(
        story=DiscussionStory(
            title="来信",
            revision_id=uuid4(),
            world=WorldSetting(premise="都市"),
            preferences=ReaderPreferences(),
            writer=WriterWorkspace(),
            characters=[],
            custom_fields=[],
            recent_scenes=[],
        ),
        history=[
            DiscussionTurn(role="user", content="我喜欢慢热"),
            DiscussionTurn(role="assistant", content="可以让人物逐步建立信任。"),
        ],
        message="下一幕如何发展？",
    )


@pytest.mark.parametrize("kind", ["reply", "search_story", "update_story_settings"])
async def test_native_optional_discussion_tools_and_normal_chat_history(kind: str) -> None:
    sink = Sink()
    model = OpenAIModel(
        Settings(_env_file=None, llm_api_key=SecretStr("test"), llm_base_url="https://api.deepseek.com/v1"),
        sink,
    )
    await model.client.aclose()
    requests: list[DeepSeekChatBody] = []
    message = ProviderMessage(role="assistant", content="可以先让人物讨论来信。")
    if kind != "reply":
        arguments = (
            '{"query":"初遇"}'
            if kind == "search_story"
            else '{"reason":"用户要求","patch":{"world":{"premise":"新都市"}}}'
        )
        message = ProviderMessage(
            role="assistant",
            tool_calls=[ToolCall(id="call1", function=FunctionCall(name=kind, arguments=arguments))],
        )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(DeepSeekChatBody.model_validate_json(request.content))
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[Choice(message=message, finish_reason="stop" if kind == "reply" else "tool_calls")]
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://api.deepseek.com/v1/"
    )
    try:
        result = await model.choose_discussion_tool(context(), uuid4())
        assert isinstance(
            result,
            {
                "reply": ReplyAction,
                "search_story": SearchStoryAction,
                "update_story_settings": UpdateSettingsAction,
            }[kind],
        )
        assert requests[0].tool_choice == "auto" and requests[0].thinking.type == "disabled"
        assert [item.function.name for item in requests[0].tools] == ["search_story", "update_story_settings"]
        assert "讨论与设定助手" in requests[0].messages[0].content
        assert requests[0].messages[1].content.startswith("协议约束：")
        assert requests[0].messages[2].content == "我喜欢慢热"
        assert requests[0].messages[3].role == "assistant"
        assert requests[0].messages[4].role == "user"
        assert sink.traces[0].error is None
    finally:
        await model.close()


async def test_unknown_discussion_tool_retries_before_any_business_execution() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(_env_file=None, llm_api_key=SecretStr("test")), sink)
    await model.client.aclose()
    count = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        message = (
            ProviderMessage(
                role="assistant",
                tool_calls=[ToolCall(id="bad", function=FunctionCall(name="rewrite_prose", arguments="{}"))],
            )
            if count == 1
            else ProviderMessage(role="assistant", content="讨论不会改写正文。")
        )
        return httpx.Response(200, content=ChatResponse(choices=[Choice(message=message)]).model_dump_json())

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )
    try:
        result = await model.choose_discussion_tool(context(), uuid4())
        assert isinstance(result, ReplyAction) and count == 2
        assert sink.traces[0].error and sink.traces[1].error is None
    finally:
        await model.close()


def test_discussion_tool_schema_cannot_carry_prose_edits() -> None:
    with pytest.raises(ValidationError):
        UpdateSettingsArguments.model_validate_json('{"reason":"修改","scenes":[{"text":"新正文"}]}')
