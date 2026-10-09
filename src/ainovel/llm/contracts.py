"""Typed internal request contracts and result interpretation strategies."""

from typing import Protocol, TypeVar

from pydantic import BaseModel

from ainovel.core.base import Model
from ainovel.llm.schemas import ChatBody, ProviderMessage

T = TypeVar("T", bound=BaseModel, covariant=True)


class CallMetadata(Model):
    node: str
    summary: str
    prompt_version: str
    error_label: str


class CompletionRequest(Model):
    body: ChatBody
    metadata: CallMetadata


class ResultDecoder(Protocol[T]):
    @property
    def repair_instruction(self) -> str: ...
    def decode(self, message: ProviderMessage) -> T: ...
