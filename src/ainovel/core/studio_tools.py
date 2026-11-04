"""Small native-tool parameters; no storage IDs required when creating entities."""

from typing import Literal

from pydantic import Field, JsonValue

from ainovel.core.base import Model
from ainovel.core.entities import Attribute, Description, EntityInput, Visibility
from ainovel.core.studio import Patch, StudioAction
from ainovel.llm.errors import StructuredOutputError


class Say(Model):
    """发送消息，不结束运行；GM 发给用户的消息不会进入角色上下文。"""

    text: str = Field(min_length=1, description="要发送的完整内容。")


class SayToUser(Say):
    """在执行途中向用户发送幕后讨论、进度说明或创作问题，消息会立即展示，发言后可继续调用工具；也可用普通回复向用户回答并结束。角色完全看不到。角色可感知的场景、台词和客观行动结果请用 say_to_group。"""

    text: str = Field(min_length=1, description="发给现实用户的创作讨论或问题；不是对角色说的话。")


class SayToGroup(Say):
    """只发到当前幕角色群聊：描述场景、外部事件、角色听见的台词和客观行动结果。在场角色可见。不得含用户幕后要求或秘密；不会写入用户讨论。发言不结束运行。"""

    text: str = Field(min_length=1, description="当前在场角色能够感知的场景、台词或行动结果。")


class Schedule(Model):
    """在用户要求开始/继续演绎，或已授权的演绎范围内，按顺序调度在场角色。仅讨论问题或修改设定时不调用。返回行动意图后由 GM 描述结果。"""

    characters: list[str] = Field(
        min_length=1, max_length=12, description="在场角色的名字或已有 ID，按运行顺序排列。"
    )
    direction: str = Field(
        default="", description="角色可感知的调度信息；不得包含幕后要求、别人私密信息或强制选择。"
    )


class Edit(Model):
    """增删改资料字段；目标可以用名字定位；创建字段无需生成 ID。设定编辑在重置后保留。"""

    scope: Literal["world", "characters", "creation", "chapters", "scenes"] = Field(
        description="五类资料之一。"
    )
    target: str = Field(default="", description="已有实体的名字或 ID；world/creation 留空时针对实体列表。")
    operations: list[Patch] = Field(
        min_length=1, max_length=50, description="相对目标的 JSON Patch 操作列表。"
    )


class SelfEdit(Model):
    """修改自己的可见描述或变量；仅在当前幕生效，演绎重置后撤销。"""

    operations: list[Patch] = Field(
        min_length=1, max_length=20, description="针对自身可见 Entity 的描述/变量条目进行 patch。"
    )


class Reset(Model):
    """清除本次演绎和临时角色变化；保留用户/GM 编辑的设定。"""

    reason: str = Field(default="", description="为何重新演绎。")


class Membership(Model):
    """安排已有角色进场或离场，不创建或删除人物实体。"""

    character: str = Field(description="角色名字或已有 ID。")
    reason: str = Field(default="", description="角色可感知的进出场说明。")


class Leave(Model):
    """主动离开群聊，之后不再接收事件或调度，直到 GM 安排重新入场。"""

    reason: str = Field(default="", description="离场说明。")


class CreateCharacter(EntityInput):
    """创建人物资料，ID 由程序生成；默认不加入当前群聊。"""


class CreationSay(Say):
    """向用户说明本次整理的构思或提出问题，发言后可以继续整理资料。"""


class CreationEdit(Edit):
    """按用户提供的信息增删改构思资料字段，使用 JSON Patch，无需一次填写完整。"""

    scope: Literal["world", "characters", "creation"] = Field(description="世界观、角色资料或创作要求。")


class CreationDescription(Description):
    visibility: Visibility = Field(
        default=Visibility.PUBLIC, description="资料可见性：0 仅系统；1 仅所属角色；2 公开。"
    )


class CreationAttribute(Attribute):
    visibility: Visibility = Field(
        default=Visibility.PUBLIC, description="变量可见性：0 仅系统；1 仅所属角色；2 公开。"
    )


class CreationCharacter(EntityInput):
    """根据用户构思创建人物资料，标识由程序生成；未知信息可以留待后续交流补充。"""

    description: list[CreationDescription] = Field(default_factory=list, description="人物的描述性资料。")
    special_attributes: list[CreationAttribute] = Field(
        default_factory=list, description="人物需要记录的变量。"
    )


TOOLS = {
    "say_to_user": SayToUser,
    "say_to_group": SayToGroup,
    "schedule_characters": Schedule,
    "edit_fields": Edit,
    "reset_performance": Reset,
    "enter_character": Membership,
    "remove_character": Membership,
    "create_character": CreateCharacter,
    "edit_self": SelfEdit,
    "leave": Leave,
}


def tools_for(mode):
    if mode == "creation":
        return {
            "say_to_user": CreationSay,
            "edit_fields": CreationEdit,
            "create_character": CreationCharacter,
        }
    names = (
        (
            "say_to_user",
            "say_to_group",
            "schedule_characters",
            "edit_fields",
            "reset_performance",
            "enter_character",
            "remove_character",
            "create_character",
        )
        if mode == "gm"
        else ("edit_self", "leave")
        if mode == "role"
        else ()
    )
    return {name: TOOLS[name] for name in names}


class StudioDecoder:
    repair_instruction = (
        "可以按执行顺序调用多个已提供的工具，每个工具都需填写合法参数；也可以直接返回文本结束本轮。"
    )

    def __init__(self, mode):
        self.mode = mode
        self.allowed = tools_for(mode)
        if mode == "gm":
            self.repair_instruction = "可以按顺序调用多个合法工具；普通文本默认回复用户，没有工具调用时自然结束；执行途中也可用 say_to_user 发言。角色可感知的内容必须用 say_to_group。"

    def decode(self, message):
        if not message.tool_calls:
            if not message.content and self.mode != "gm":
                raise StructuredOutputError("没有返回文本或工具")
            return StudioAction(name="reply", text=message.content or "")
        from ainovel.core.studio import StudioStep

        actions = []
        ids = set()
        for call in message.tool_calls:
            if not call.id or call.id in ids:
                raise StructuredOutputError("工具调用 ID 为空或重复")
            ids.add(call.id)
            if call.function.name not in self.allowed:
                raise StructuredOutputError("当前角色不能使用该工具")
            args = self.allowed[call.function.name].model_validate_json(call.function.arguments)
            actions.append(
                StudioAction(name=call.function.name, call_id=call.id, arguments=args.model_dump(mode="json"))
            )
        return StudioStep(actions=actions, text=message.content or "")


class TextResult(Model):
    text: str


class TextDecoder:
    repair_instruction = "请直接返回完整文本，不调用工具。"

    def decode(self, message):
        if message.tool_calls or not message.content:
            raise StructuredOutputError("此任务需要完整文本")
        return TextResult(text=message.content)


def tool_schemas(mode) -> list[dict[str, JsonValue]]:
    return [
        {"name": name, "description": cls.__doc__ or name, "parameters": cls.model_json_schema()}
        for name, cls in tools_for(mode).items()
    ]
