"""Exact, atomic edits against a single immutable text version."""

from pydantic import Field

from ainovel.core.base import Model


class TextEdit(Model):
    original: str = Field(
        min_length=1, description="逐字复制待修改原文；必须在当前草稿中唯一出现，重复时扩大到包含上下文的片段"
    )
    replacement: str = Field(description="仅替换该片段的新正文；空字符串表示删除")
    reason: str = Field(min_length=1, description="这处修改对应的用户意见与原因")


class LocatedEdit(Model):
    edit_number: int
    start: int
    end: int
    replacement: str


class TextEditConflict(ValueError):
    """The model's edit list cannot be applied to this source version."""


def apply_text_edits(source: str, edits: list[TextEdit]) -> str:
    """Validate every anchor and overlap before changing any text."""
    located: list[LocatedEdit] = []
    for number, edit in enumerate(edits, start=1):
        start = source.find(edit.original)
        if start < 0:
            raise TextEditConflict(f"第 {number} 处修改原文与当前草稿不一致，需逐字复制当前原文")
        if source.find(edit.original, start + 1) >= 0:
            raise TextEditConflict(f"第 {number} 处修改原文在草稿中重复，需提供包含上下文的唯一原文片段")
        located.append(
            LocatedEdit(
                edit_number=number, start=start, end=start + len(edit.original), replacement=edit.replacement
            )
        )
    located.sort(key=lambda edit: edit.start)
    cursor = 0
    pieces: list[str] = []
    previous: LocatedEdit | None = None
    for edit in located:
        if previous is not None and edit.start < previous.end:
            raise TextEditConflict(
                f"第 {previous.edit_number} 与第 {edit.edit_number} 处修改片段互相重叠；请合并为一处修改，或缩小范围使它们不再重叠"
            )
        pieces.extend((source[cursor : edit.start], edit.replacement))
        cursor = edit.end
        previous = edit
    pieces.append(source[cursor:])
    return "".join(pieces)
