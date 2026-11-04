
from uuid import UUID, uuid4

import httpx
import pytest
from configured_fields import make_world, story_from_proposal
from pydantic import SecretStr

from ainovel.core.config import Settings
from ainovel.core.domain import (
    Chapter,
    CreationProposal,
    Draft,
    Model,
    NarrativeContext,
    PromptTemplateVersion,
    Scene,
    Trace,
    WritingState,
)
from ainovel.llm.client import OpenAIModel
from ainovel.llm.errors import StructuredOutputError
from ainovel.llm.schemas import (
    ChatBody,
    ChatResponse,
    Choice,
    FinalizationResult,
    FunctionCall,
    NarrativeInput,
    ProviderMessage,
    ProviderUsage,
    TokenDetails,
    ToolCall,
)


def tool_message(arguments: str | None, name: str = "submit_Draft") -> ProviderMessage:
    return ProviderMessage(
        role="assistant",
        tool_calls=[ToolCall(id="call_test", function=FunctionCall(name=name, arguments=arguments))]
        if arguments is not None
        else [],
    )


async def test_provider_reasoning_is_preserved_only_when_returned() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), _env_file=None), sink)
    await model.client.aclose()
    draft = Draft(title="来信", text="正文", summary="摘要")
    message = tool_message(draft.model_dump_json())
    message.reasoning_content = "供应商返回的推理内容"

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ChatResponse(choices=[Choice(message=message)]).model_dump_json())

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )
    try:
        assert await model.generate(Draft, "writing", Input(instruction="开始"), uuid4()) == draft
        assert sink.traces[0].reasoning_content == message.reasoning_content
        assert Trace(node="无推理", summary="正常输出").reasoning_content == ""
    finally:
        await model.close()


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [
        ('{"title":"来信","text":"正文","summary":"摘要"', "stop"),
        ('{"title":"来信","summary":"摘要"}', "stop"),
        ('{"title":"来信","text":"正文","summary":"摘要"}', "length"),
        (None, "stop"),
    ],
)
async def test_invalid_structured_output_retries_with_audited_raw_response(
    content: str | None, finish_reason: str
) -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), llm_max_tokens=2048, _env_file=None), sink)
    await model.client.aclose()
    expected = Draft(title="来信", text="正文", summary="摘要")
    calls: list[ChatBody] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(ChatBody.model_validate_json(request.content))
        first = len(calls) == 1
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[
                    Choice(
                        message=tool_message(content if first else expected.model_dump_json()),
                        finish_reason=finish_reason if first else "stop",
                    )
                ],
                usage=ProviderUsage(prompt_tokens=10, completion_tokens=5),
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )
    try:
        assert await model.generate(Draft, "writing", Input(instruction="开始"), uuid4()) == expected
        assert len(calls) == len(sink.traces) == 2
        assert calls[0].max_tokens == 2048 and calls[1].max_tokens == 4096
        assert calls[1].messages[-1].role == "user"
        assert sink.traces[0].structured_output == (content or "")
        assert sink.traces[0].raw_output
        assert sink.traces[1].tool_name == "submit_Draft" and sink.traces[1].tool_call_id == "call_test"
        assert sink.traces[0].finish_reason == finish_reason
        assert sink.traces[0].error and sink.traces[1].error is None
        assert [trace.attempt for trace in sink.traces] == [1, 2]
        assert sum(trace.usage.output_tokens for trace in sink.traces) == 10
    finally:
        await model.close()


async def test_invalid_json_retry_is_bounded_and_reports_a_readable_error() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), llm_output_retries=2, _env_file=None), sink)
    await model.client.aclose()
    response = ChatResponse(choices=[Choice(message=tool_message("{"), finish_reason="tool_calls")])
    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=response.model_dump_json())
        ),
        base_url="https://example.test/v1/",
    )
    try:
        with pytest.raises(StructuredOutputError, match="已自动重试 2 次"):
            await model.generate(Draft, "writing", Input(instruction="开始"), uuid4())
        assert len(sink.traces) == 3 and all(trace.error for trace in sink.traces)
        assert all(trace.structured_output == "{" for trace in sink.traces)
    finally:
        await model.close()


async def test_network_failure_does_not_trigger_structured_output_retries() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), _env_file=None), sink)
    await model.client.aclose()

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    model.client = httpx.AsyncClient(transport=httpx.MockTransport(fail), base_url="https://example.test/v1/")
    try:
        with pytest.raises(httpx.ConnectError):
            await model.generate(Draft, "writing", Input(instruction="开始"), uuid4())
        assert len(sink.traces) == 1 and sink.traces[0].error
    finally:
        await model.close()


class Sink:
    def __init__(self) -> None:
        self.traces: list[Trace] = []

    async def record(self, command_id: UUID, trace: Trace) -> None:
        self.traces.append(trace)


class Input(Model):
    instruction: str


@pytest.mark.parametrize("invalid", ["text_only", "wrong_name", "multiple_calls"])
async def test_only_one_designated_tool_result_is_accepted(invalid: str) -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), llm_output_retries=1, _env_file=None), sink)
    await model.client.aclose()
    expected = Draft(title="来信", text="正文", summary="摘要")
    bad = tool_message(expected.model_dump_json())
    if invalid == "text_only":
        bad.tool_calls = []
        bad.content = expected.model_dump_json()
    elif invalid == "wrong_name":
        bad.tool_calls[0].function.name = "delete_story"
    else:
        bad.tool_calls.append(
            ToolCall(
                id="call_second",
                function=FunctionCall(name="submit_Draft", arguments=expected.model_dump_json()),
            )
        )
    calls: list[ChatBody] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(ChatBody.model_validate_json(request.content))
        message = bad if len(calls) == 1 else tool_message(expected.model_dump_json())
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[Choice(message=message, finish_reason="tool_calls")]
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )
    try:
        assert await model.generate(Draft, "writing", Input(instruction="开始"), uuid4()) == expected
        assert len(calls) == 2 and sink.traces[0].error
        assert invalid != "wrong_name" or "delete_story" in sink.traces[0].raw_output
        assert all(body.tool_choice.function.name == "submit_Draft" for body in calls)
    finally:
        await model.close()


async def test_deepseek_thinking_config_is_checked_before_forcing_tool_calls() -> None:
    sink = Sink()
    model = OpenAIModel(
        Settings(
            llm_api_key=SecretStr("test"), llm_model="deepseek-flash", llm_thinking="enabled", _env_file=None
        ),
        sink,
    )
    try:
        with pytest.raises(ValueError, match="LLM_THINKING=disabled"):
            await model.generate(Draft, "writing", Input(instruction="开始"), uuid4())
        assert not sink.traces
    finally:
        await model.close()


async def test_real_adapter_validates_assistant_response_and_records_usage() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), llm_model="test-model", _env_file=None), sink)
    await model.client.aclose()
    expected = Draft(title="来信", text="正文", summary="摘要")

    def response(request: httpx.Request) -> httpx.Response:
        body = ChatBody.model_validate_json(request.content)
        assert body.tools[0].function.parameters.root == Draft.model_json_schema()
        assert body.tool_choice.function.name == body.tools[0].function.name == "submit_Draft"
        assert "response_format" not in body.model_dump()
        assert "context_field_schema" in body.messages[1].content
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[
                    Choice(message=tool_message(expected.model_dump_json()), finish_reason="tool_calls")
                ],
                usage=ProviderUsage(
                    prompt_tokens=100,
                    completion_tokens=30,
                    prompt_tokens_details=TokenDetails(cached_tokens=20),
                ),
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(response), base_url="https://example.test/v1/"
    )
    try:
        result = await model.generate(Draft, "writing", Input(instruction="开始"), uuid4())
        assert result == expected
        assert sink.traces[0].usage.input_tokens == 100
        assert sink.traces[0].usage.output_tokens == 30
        assert sink.traces[0].usage.cached_tokens == 20
        assert "writing:v2:" in sink.traces[0].prompt_version
    finally:
        await model.close()


async def test_full_prompt_source_is_rendered_once_pinned_in_trace_and_kept_across_retry() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), llm_max_tokens=2048, _env_file=None), sink)
    await model.client.aclose()
    expected = Draft(title="来信", text="正文", summary="摘要")
    bodies: list[ChatBody] = []

    def respond(request: httpx.Request) -> httpx.Response:
        bodies.append(ChatBody.model_validate_json(request.content))
        result = '{"title":"来信","text":"正文"}' if len(bodies) == 1 else expected.model_dump_json()
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[Choice(message=tool_message(result), finish_reason="tool_calls")]
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )

    template = PromptTemplateVersion(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        scope="global",
        scope_id=UUID("22222222-2222-2222-2222-222222222222"),
        task="writing",
        name="测试完整模板",
        version=7,
        source_format="jinja2",
        source="你是{{ story_title }}的作者，要求：{{ instruction }}。",
    )
    try:
        assert (
            await model.generate(
                Draft,
                "writing",
                Input(instruction="保留 {{ 未渲染内容 }}"),
                uuid4(),
                prompt_template=template,
            )
            == expected
        )
        assert len(bodies) == len(sink.traces) == 2
        assert bodies[0].messages[0].content == "你是的作者，要求：保留 {{ 未渲染内容 }}。"
        assert bodies[0].messages[0].content == bodies[1].messages[0].content
        assert "context_field_schema" in bodies[0].messages[1].content
        assert '{"instruction":"保留 {{ 未渲染内容 }}"}' in bodies[0].messages[2].content
        assert all("writing:v7:" in trace.prompt_version for trace in sink.traces)
    finally:
        await model.close()


async def test_finalization_missing_optional_explanation_does_not_retry_or_drop_updates() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), _env_file=None), sink)
    await model.client.aclose()
    character_id = uuid4()
    arguments = (
        '{"extraction":{"updates":[{"character_id":"' + str(character_id) + '","reason":"正文有依据"}]}}'
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ChatResponse(
                choices=[
                    Choice(
                        message=tool_message(arguments, "submit_FinalizationResult"),
                        finish_reason="tool_calls",
                    )
                ]
            ).model_dump_json(),
        )

    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test/v1/"
    )
    try:
        result = await model.generate(
            FinalizationResult, "writing", Input(instruction="整理正文事实"), uuid4()
        )
        assert result.extraction.decision_summary == ""
        assert result.extraction.updates[0].character_id == character_id
        assert result.extraction.updates[0].reason == "正文有依据"
        assert len(sink.traces) == 1 and not sink.traces[0].error
    finally:
        await model.close()


def test_finalization_still_requires_the_actual_update_list() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FinalizationResult.model_validate_json('{"extraction":{"decision_summary":"只返回说明"}}')


def test_narrative_prompt_excludes_unretrieved_old_chapters_and_snapshot_histories() -> None:
    chapter = Chapter(title="第一章")
    old = Scene(chapter_id=chapter.id, title="旧场景", text="不应自动注入的早期正文", summary="旧")
    recent = Scene(chapter_id=chapter.id, title="近期", text="近期正文", summary="近期")
    state = WritingState(
        command_id=uuid4(),
        story=story_from_proposal(proposal=CreationProposal(title="书", world=make_world('城')), chapters=[chapter], scenes=[old, recent]),
        instruction="继续",
        context=NarrativeContext(recent_scenes=[recent]),
    )
    content = NarrativeInput.from_state(state).model_dump_json()
    assert "近期正文" in content
    assert "不应自动注入的早期正文" not in content
    assert "snapshots" not in content


async def test_provider_optional_wire_fields_do_not_break_domain_validation() -> None:
    sink = Sink()
    model = OpenAIModel(Settings(llm_api_key=SecretStr("test"), llm_model="test-model", _env_file=None), sink)
    await model.client.aclose()
    wire = (
        '{"choices":[{"index":0,"finish_reason":"tool_calls","logprobs":null,"message":'
        '{"role":"assistant","refusal":null,"annotations":[],'
        '"content":null,"tool_calls":[{"index":0,"id":"call_test","type":"function","function":'
        '{"name":"submit_Draft","arguments":"{\\"title\\":\\"来信\\",\\"text\\":\\"正文\\",\\"summary\\":\\"摘要\\"}"}}]}}],'
        '"usage":{"prompt_tokens":11,"completion_tokens":7}}'
    )
    model.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=wire)),
        base_url="https://example.test/v1/",
    )
    try:
        result = await model.generate(Draft, "writing", Input(instruction="开始"), uuid4())
        assert result.text == "正文"
        assert sink.traces[0].usage.input_tokens == 11
    finally:
        await model.close()
