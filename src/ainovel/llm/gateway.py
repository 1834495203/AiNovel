"""Single request, validation, retry and audit pipeline for model services."""

import json
from collections.abc import Callable
from time import perf_counter
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from ainovel.core.config import Settings
from ainovel.core.domain import Trace, Usage
from ainovel.core.ports import TraceSink
from ainovel.llm.contracts import CallMetadata, CompletionRequest, ResultDecoder
from ainovel.llm.errors import StructuredOutputError
from ainovel.llm.schemas import ChatBody, ChatResponse, EmbeddingBody, EmbeddingResponse, Message
from ainovel.llm.transport import Endpoint, ModelTransport

T = TypeVar("T", bound=BaseModel)


class ModelGateway:
    def __init__(self, settings: Settings, sink: TraceSink, transport: ModelTransport) -> None:
        self.settings, self.sink, self.transport = settings, sink, transport

    async def close(self) -> None:
        await self.transport.close()

    async def complete(self, request: CompletionRequest, decoder: ResultDecoder[T], command_id: UUID) -> T:
        body = self.transport.prepare_chat(request.body)
        for attempt in range(1, self.settings.llm_output_retries + 2):
            try:

                def parse(raw: str, trace: Trace) -> T:
                    response = ChatResponse.model_validate_json(raw)
                    trace.usage = Usage(
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                        cached_tokens=response.usage.prompt_tokens_details.cached_tokens
                        if response.usage.prompt_tokens_details
                        else response.usage.prompt_cache_hit_tokens,
                    )
                    if not response.choices:
                        raise StructuredOutputError("模型返回空结果")
                    choice = response.choices[0]
                    trace.finish_reason = choice.finish_reason
                    trace.reasoning_content = choice.message.reasoning_content or ""
                    if choice.message.tool_calls:
                        first = choice.message.tool_calls[0]
                        trace.tool_name, trace.tool_call_id = first.function.name, first.id
                        trace.structured_output = first.function.arguments
                    if choice.finish_reason == "length":
                        raise StructuredOutputError("模型输出达到 token 上限，返回内容可能被截断")
                    if choice.finish_reason == "content_filter":
                        raise ValueError("模型供应商未返回内容，结束原因为 content_filter")
                    result = decoder.decode(choice.message)
                    trace.structured_output = result.model_dump_json()
                    return result

                return await self._request(
                    "chat/completions", body, request.metadata, command_id, attempt, parse
                )
            except (ValidationError, StructuredOutputError) as error:
                if attempt > self.settings.llm_output_retries:
                    raise StructuredOutputError(
                        f"模型未通过 {request.metadata.error_label} 工具参数校验，已自动重试 {attempt - 1} 次。"
                        f"原因：{self.output_error_reason(error)}。这次回复未执行；此前已保存的进度仍保留。"
                    ) from error
                details = (
                    json.dumps(
                        error.errors(include_input=False, include_url=False), ensure_ascii=False, default=str
                    )[:2000]
                    if isinstance(error, ValidationError)
                    else str(error)
                )
                body.messages.append(
                    Message(
                        role="user", content=f"上次返回未通过校验：{details}。{decoder.repair_instruction}"
                    )
                )
                body.max_tokens = min(self.settings.llm_max_tokens * 2, 65536)
        raise AssertionError("Unreachable model retry state")

    async def _request(
        self,
        endpoint: Endpoint,
        body: ChatBody | EmbeddingBody,
        metadata: CallMetadata,
        command_id: UUID,
        attempt: int,
        parse: Callable[[str, Trace], T],
    ) -> T:
        started = perf_counter()
        trace = Trace(
            node=metadata.node,
            summary=metadata.summary,
            model=body.model,
            prompt_version=metadata.prompt_version,
            structured_input=body.model_dump_json(),
            attempt=attempt,
        )
        try:
            trace.raw_output = await self.transport.post(endpoint, body)
            return parse(trace.raw_output, trace)
        except Exception as error:
            trace.error = str(error)
            raise
        finally:
            trace.duration_ms = int((perf_counter() - started) * 1000)
            await self.sink.record(command_id, trace)

    async def embed(self, texts: list[str], command_id: UUID) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 32):
            body = EmbeddingBody(
                model=self.settings.embedding_model,
                input=texts[start : start + 32],
                dimensions=self.settings.embedding_dimensions,
            )

            def parse(raw: str, trace: Trace) -> EmbeddingResponse:
                response = EmbeddingResponse.model_validate_json(raw)
                trace.usage = Usage(input_tokens=response.usage.prompt_tokens, output_tokens=0)
                rows = sorted(response.data, key=lambda item: item.index)
                if [row.index for row in rows] != list(range(len(body.input))) or any(
                    len(row.embedding) != body.dimensions for row in rows
                ):
                    raise ValueError("embedding 数量、索引或维度不符合配置")
                trace.structured_output = response.model_dump_json()
                return response

            metadata = CallMetadata(
                node="embedding", summary="建立/查询语义向量", prompt_version="v1", error_label="embedding"
            )
            response = await self._request("embeddings", body, metadata, command_id, 1, parse)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
        return vectors

    @staticmethod
    def output_error_reason(error: ValidationError | StructuredOutputError) -> str:
        if not isinstance(error, ValidationError):
            return str(error)
        reasons: list[str] = []
        for issue in error.errors(include_input=False, include_url=False)[:3]:
            path = ".".join(str(part) for part in issue["loc"])
            if issue["type"] == "json_invalid":
                reasons.append("工具参数不是合法 JSON")
            elif issue["type"] == "missing":
                reasons.append(f"缺少必填字段 {path}")
            elif issue["type"] == "extra_forbidden":
                reasons.append(f"包含未定义的字段 {path}")
            else:
                reasons.append(f"字段 {path or '结果'} 的类型或取值不符合约束")
        return "；".join(reasons)
