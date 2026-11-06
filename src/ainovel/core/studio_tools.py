"""Small native-tool parameters; no storage IDs required when creating entities."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from ainovel.core.base import Model
from ainovel.core.entities import Attribute, Description, EntityInput, Visibility
from ainovel.core.prose_edits import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ProseEdit
from ainovel.core.studio import NamedFieldEdit, Patch, StudioAction
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


class CharacterDispatch(Model):
    character: str = Field(min_length=1, description="在场角色的名字或已有 ID。")
    direction: str = Field(default="", description="只发给这一项角色的提示词，可留空。")


class Schedule(Model):
    """在用户要求开始/继续演绎，或已授权的演绎范围内，按顺序调度在场角色。仅讨论问题或修改设定时不调用。返回行动意图后由 GM 描述结果。"""

    characters: list[CharacterDispatch | str] = Field(
        min_length=1,
        max_length=12,
        description="按运行顺序排列。每个角色可填写 character 和自己的 direction；无需提示时也可只填角色名字或 ID。",
    )
    direction: str = Field(
        default="",
        description="兼容旧调用的共用提示词，仅当 characters 全部是字符串时可填写。新调用请给每个角色单独设置 direction。",
    )

    @model_validator(mode="after")
    def validate_directions(self):
        if self.direction.strip() and any(isinstance(item, CharacterDispatch) for item in self.characters):
            raise ValueError("使用角色调度项时，请将提示词填在对应角色的 direction 中")
        return self


class Edit(Model):
    """增删改资料字段；目标可以用名字定位；创建字段无需生成 ID。设定编辑在重置后保留。"""

    scope: Literal["world", "characters", "creation", "chapters", "scenes"] = Field(
        description="五类资料之一。"
    )
    target: str = Field(default="", description="已有实体的名字或 ID；world/creation 留空时针对实体列表。")
    operations: list[NamedFieldEdit | Patch] = Field(
        min_length=1,
        max_length=50,
        description="修改现有字段时优先使用 set_field 和字段名，删除用 remove_field。新增字段等操作支持 JSON Patch。",
    )


class SelfEdit(Model):
    """修改自己的可见描述或变量；仅在当前幕生效，演绎重置后撤销。"""

    operations: list[NamedFieldEdit | Patch] = Field(
        min_length=1,
        max_length=20,
        description="可用 set_field 或 remove_field 按名称操作自己的可见字段，也支持 JSON Patch。",
    )


class Reset(Model):
    """清除本次演绎和临时角色变化；保留用户/GM 编辑的设定。"""

    reason: str = Field(default="", description="为何重新演绎。")


class RewindPerformance(Model):
    """回撤当前未确认幕的演绎。按步数回撤，或回到指定操作/群聊消息之后；统一重放正向操作，连同字段、角色进出场和在场人数一起恢复。用户讨论不删除。"""

    steps: int | None = Field(
        default=None,
        ge=1,
        description="回撤多少个状态操作；不填且无 target 时回撤一步。不能与 target 同时填写。",
    )
    target: str = Field(
        default="",
        description="用 #7 指第 7 条演绎事件，用 step:3 指第 3 步回撤记录。也接受现有操作或事件 ID。"
        "保留目标及之前的操作；start 表示回到开始记录的位置。只接受当前路径中的位置。",
    )

    @model_validator(mode="after")
    def choose_one_position(self):
        if self.steps is not None and self.target:
            raise ValueError("steps 与 target 只能填写一个")
        return self


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
    """按用户提供的信息编辑构思资料。支持按字段名修改，也支持 JSON Patch。"""

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


class ReadProse(Model):
    """按段读取本幕正文，或搜索关键词并返回附近原文。段落编号从 1 开始，结果附带正文版本。"""

    start: int = Field(default=1, ge=1, description="起始段号；搜索时是命中段落的起始序号。")
    count: int | Literal["all"] | None = Field(
        default=None,
        description=f"希望读取的段数，留空或 null 时读 {DEFAULT_PAGE_SIZE} 段。"
        f"每页最多 {MAX_PAGE_SIZE} 段，all 表示尽量读满一页。继续读取时使用返回的 next_start。",
    )
    query: str = Field(default="", max_length=500, description="可选。按原文查找关键词，留空时读取完整段落。")


class ReadWritingScene(Model):
    """读取本幕已确认的演绎、角色状态和上一版写作要求。初次生成或整幕重写时先调用。"""


class EditProse(Model):
    """按精确原文批量提交局部编辑，逐条定位后一起应用。其他文字和换行原样保留。"""

    revision: str = Field(description="当前正文版本，从 read_prose 或上下文原样复制。")
    edits: list[ProseEdit] = Field(min_length=1, max_length=30, description="各条都基于同一版本的原文。")


class WriteProse(Model):
    """首次写作或整幕重写时提交完整正文。局部修改使用 edit_prose。"""

    revision: str = Field(description="当前正文版本，从上下文或 read_prose 原样复制。")
    text: str = Field(min_length=1, description="整幕小说正文，保留自然段。")


class WritingEdit(Edit):
    """编辑「创作」资料，支持按名称修改字段和 JSON Patch。正文编辑使用专门的工具。"""

    scope: Literal["creation"] = Field(default="creation", description="只允许操作创作资料。")


TOOLS = {
    "say_to_user": SayToUser,
    "say_to_group": SayToGroup,
    "schedule_characters": Schedule,
    "edit_fields": Edit,
    "reset_performance": Reset,
    "rewind_performance": RewindPerformance,
    "enter_character": Membership,
    "remove_character": Membership,
    "create_character": CreateCharacter,
    "edit_self": SelfEdit,
    "leave": Leave,
}


def tools_for(mode):
    if mode == "writing_chat":
        return {
            "read_prose": ReadProse,
            "read_scene": ReadWritingScene,
            "edit_prose": EditProse,
            "write_prose": WriteProse,
            "edit_fields": WritingEdit,
        }
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
            "rewind_performance",
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
