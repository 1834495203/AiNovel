from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel
from test_discussion_llm import context as discussion_context
from test_llm import Input, Sink, tool_message

from ainovel.core.base import Model
from ainovel.core.config import Settings
from ainovel.core.discussion import ReplyAction, SearchStoryAction
from ainovel.core.domain import Draft
from ainovel.llm.schemas import (
    ChatBody,
    ChatResponse,
    Choice,
    EmbeddingBody,
    EmbeddingItem,
    EmbeddingResponse,
    EmbeddingUsage,
    ProviderMessage,
    ProviderUsage,
)
from ainovel.llm.service import LLMService
from ainovel.llm.transport import Endpoint


class RecordedRequest(Model):
    endpoint: Endpoint
    body: ChatBody | EmbeddingBody


class MemoryTransport:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.requests: list[RecordedRequest] = []
        self.closed = False

    def prepare_chat(self, body: ChatBody) -> ChatBody:
        return body

    async def post(self, endpoint: Endpoint, body: BaseModel) -> str:
        assert isinstance(body, (ChatBody, EmbeddingBody))
        self.requests.append(RecordedRequest(endpoint=endpoint, body=body.model_copy(deep=True)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def completion(message: ProviderMessage, finish: str = "tool_calls") -> str:
    return ChatResponse(
        choices=[Choice(message=message, finish_reason=finish)],
        usage=ProviderUsage(prompt_tokens=10, completion_tokens=5, prompt_cache_hit_tokens=2),
    ).model_dump_json()


async def test_all_model_modes_share_injected_transport_audit_and_lifecycle() -> None:
    draft = Draft(title="幕", text="正文", summary="摘要")
    embedding = EmbeddingResponse(
        data=[EmbeddingItem(index=0, embedding=[1.0] * 8)], usage=EmbeddingUsage(prompt_tokens=3)
    )
    transport = MemoryTransport(
        [
            completion(tool_message(draft.model_dump_json())),
            completion(tool_message('{"query":"前文"}', "search_story")),
            completion(ProviderMessage(role="assistant", content="可以继续推进。"), "stop"),
            embedding.model_dump_json(),
        ]
    )
    sink = Sink()
    service = LLMService(Settings(_env_file=None, embedding_dimensions=8), sink, transport=transport)
    command_id = uuid4()
    assert await service.generate(Draft, "writing", Input(instruction="继续"), command_id) == draft
    assert isinstance(
        await service.choose_discussion_tool(discussion_context(), command_id), SearchStoryAction
    )
    assert isinstance(await service.choose_discussion_tool(discussion_context(), command_id), ReplyAction)
    assert await service.embed(["前文"], command_id) == [[1.0] * 8]
    assert [item.endpoint for item in transport.requests] == ["chat/completions"] * 3 + ["embeddings"]
    assert [trace.node for trace in sink.traces] == ["Draft", "discussion", "discussion", "embedding"]
    assert all(trace.raw_output and trace.structured_output and not trace.error for trace in sink.traces)
    assert sum(trace.usage.input_tokens for trace in sink.traces) == 33
    await service.close()
    assert transport.closed


@pytest.mark.parametrize("mode", ["structured", "discussion"])
@pytest.mark.parametrize("failure", ["invalid_json", "length", "empty_choices"])
async def test_both_completion_modes_share_bounded_repair_and_attempt_auditing(
    mode: str, failure: str
) -> None:
    valid = (
        tool_message(Draft(title="幕", text="正文", summary="摘要").model_dump_json())
        if mode == "structured"
        else ProviderMessage(role="assistant", content="讨论已恢复。")
    )
    invalid = tool_message("{", "submit_Draft" if mode == "structured" else "search_story")
    first = (
        ChatResponse(choices=[]).model_dump_json()
        if failure == "empty_choices"
        else completion(
            valid if failure == "length" else invalid, "length" if failure == "length" else "tool_calls"
        )
    )
    transport = MemoryTransport([first, completion(valid)])
    sink = Sink()
    service = LLMService(
        Settings(_env_file=None, llm_max_tokens=2048, llm_output_retries=1), sink, transport=transport
    )
    if mode == "structured":
        await service.generate(Draft, "writing", Input(instruction="继续"), uuid4())
    else:
        await service.choose_discussion_tool(discussion_context(), uuid4())
    assert [trace.attempt for trace in sink.traces] == [1, 2]
    assert sink.traces[0].error and not sink.traces[1].error
    assert sink.traces[0].prompt_version == sink.traces[1].prompt_version
    second = transport.requests[1].body
    assert isinstance(second, ChatBody) and second.max_tokens == 4096
    assert "未通过校验" in second.messages[-1].content
    await service.close()


@pytest.mark.parametrize("mode", ["structured", "discussion"])
async def test_network_errors_are_audited_once_without_output_repairs(mode: str) -> None:
    transport = MemoryTransport([httpx.ConnectError("网络失败")])
    sink = Sink()
    service = LLMService(Settings(_env_file=None), sink, transport=transport)
    with pytest.raises(httpx.ConnectError):
        if mode == "structured":
            await service.generate(Draft, "writing", Input(instruction="继续"), uuid4())
        else:
            await service.choose_discussion_tool(discussion_context(), uuid4())
    assert len(transport.requests) == len(sink.traces) == 1
    assert sink.traces[0].error == "网络失败"
    await service.close()


async def test_embedding_index_conflict_cannot_mix_up_input_vectors() -> None:
    response = EmbeddingResponse(
        data=[EmbeddingItem(index=0, embedding=[1.0] * 8), EmbeddingItem(index=0, embedding=[2.0] * 8)]
    )
    transport = MemoryTransport([response.model_dump_json()])
    sink = Sink()
    service = LLMService(Settings(_env_file=None, embedding_dimensions=8), sink, transport=transport)
    with pytest.raises(ValueError, match="索引"):
        await service.embed(["甲", "乙"], uuid4())
    assert len(sink.traces) == 1 and sink.traces[0].error
    await service.close()
