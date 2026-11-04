"""The five workspace categories share these small author-editable contracts."""

from enum import IntEnum
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from ainovel.core.base import Model


class Visibility(IntEnum):
    GOD = 0
    SELF = 1
    PUBLIC = 2


class Attribute(Model):
    key: str = Field(min_length=1, description="变量名，同一实体内唯一。")
    value_type: Literal["str", "int", "float", "bool", "list"] = Field(description="变量值的类型。")
    current_value: JsonValue = Field(description="当前变量值，必须符合声明的类型。")
    why: str = Field(default="", description="为什么定义这个变量。")
    how: str = Field(default="", description="变量的叙事表现；数值应说明不同区间的具体表现。")
    when: str = Field(default="", description="什么情形下应修改此变量。")
    visibility: Visibility = Field(
        default=Visibility.PUBLIC, description="0 仅 GM/系统；1 仅所属角色；2 公开，仍受在场范围限制。"
    )

    @model_validator(mode="after")
    def value_matches_type(self):
        value = self.current_value
        valid = {
            "str": isinstance(value, str),
            "int": type(value) is int,
            "float": type(value) in (int, float),
            "bool": type(value) is bool,
            "list": isinstance(value, list),
        }[self.value_type]
        if not valid:
            raise ValueError(f"{self.key} 的值不符合 {self.value_type}")
        return self


class Description(Model):
    name: str = Field(min_length=1, description="描述字段名称，同一实体内唯一。")
    value: list[str] = Field(default_factory=list, description="描述内容列表。")
    visibility: Visibility = Field(
        default=Visibility.PUBLIC, description="0 仅 GM/系统；1 仅所属角色；2 公开，仍受在场范围限制。"
    )


class EntityInput(Model):
    name: str = Field(min_length=1, description="实体名字。")
    type: str = Field(
        default="human", min_length=1, description="实体类型，例如 human、location、item、skill、world。"
    )
    description: list[Description] = Field(default_factory=list, description="描述性资料。")
    special_attributes: list[Attribute] = Field(default_factory=list, description="需要跟踪的变量。")

    @model_validator(mode="after")
    def unique_fields(self):
        for keys in ([x.name for x in self.description], [x.key for x in self.special_attributes]):
            if len(keys) != len(set(keys)):
                raise ValueError("同一实体内不能重复定义同名字段")
        return self


class Entity(EntityInput):
    id: str = Field(
        default_factory=lambda: str(uuid4()), description="程序生成的稳定标识，创建实体时 AI 不填写。"
    )


def visible_entity(entity: Entity, *, own: bool) -> Entity:
    result = entity.model_copy(deep=True)
    allowed = {Visibility.SELF, Visibility.PUBLIC} if own else {Visibility.PUBLIC}
    result.description = [d for d in result.description if d.visibility in allowed]
    result.special_attributes = [a for a in result.special_attributes if a.visibility in allowed]
    return result
