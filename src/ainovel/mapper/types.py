"""Typed JSON conversion at the SQLAlchemy boundary."""

from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

T = TypeVar("T", bound=BaseModel)


class ModelJSON(TypeDecorator[T], Generic[T]):
    impl = Text
    cache_ok = True

    def __init__(self, model: type[T]) -> None:
        super().__init__()
        self.model = model

    def process_bind_param(self, value: T | None, dialect: object) -> str | None:
        return value.model_dump_json() if value is not None else None

    def process_result_value(self, value: str | None, dialect: object) -> T | None:
        return self.model.model_validate_json(value) if value is not None else None
