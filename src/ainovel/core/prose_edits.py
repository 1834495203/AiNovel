"""Resolve exact prose anchors and apply a batch against one text version."""

import re
from hashlib import sha256
from typing import Literal

from pydantic import Field

from ainovel.core.base import Model

PARAGRAPH_BREAK = re.compile(r"\r?\n[^\S\r\n]*\r?\n(?:[^\S\r\n]*\r?\n)*")
DEFAULT_PAGE_SIZE = 3
MAX_PAGE_SIZE = 12


class ProseEdit(Model):
    search: str = Field(min_length=1, description="逐字引用当前正文的原文，可跨段，包含原标点和换行。")
    replacement: str = Field(description="新文字。替换为空字符串表示删除。插入需要包含所需的空格或换行。")
    paragraph: int | None = Field(default=None, ge=1, description="可选，限定 read_prose 返回的段落编号。")
    operation: Literal["replace", "insert_before", "insert_after"] = Field(
        default="replace", description="替换锚点，或在锚点前、后插入。"
    )
    replace_all: bool = Field(default=False, description="仅明确要求批量替换时设为 true，否则要求唯一命中。")


class ProseChange(Model):
    paragraph: int | None = None
    before: str
    after: str
    count: int


def prose_revision(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()[:16]


def paragraph_ranges(text: str) -> list[tuple[int, int]]:
    ranges = []
    start = 0
    separators = (
        PARAGRAPH_BREAK.finditer(text) if PARAGRAPH_BREAK.search(text) else re.finditer(r"\r?\n", text)
    )
    for separator in separators:
        if text[start : separator.start()].strip():
            ranges.append((start, separator.start()))
        start = separator.end()
    if text[start:].strip():
        ranges.append((start, len(text)))
    return ranges


def require_revision(text: str, revision: str) -> None:
    if revision != prose_revision(text):
        raise ValueError("正文版本已变化，请重新读取原文后再提交")


def apply_prose_edits(text: str, revision: str, edits: list[ProseEdit]) -> tuple[str, list[ProseChange]]:
    require_revision(text, revision)
    paragraphs = paragraph_ranges(text)
    spans = []
    changes = []
    for edit in edits:
        start, end = 0, len(text)
        if edit.paragraph is not None:
            if edit.paragraph > len(paragraphs):
                raise ValueError(f"第 {edit.paragraph} 段不存在，请重新读取段落")
            start, end = paragraphs[edit.paragraph - 1]
        first = text.find(edit.search, start, end)
        if first < 0:
            raise ValueError(f"未匹配原文 {edit.search[:80]!r}，请读取原句及标点后重试")
        if edit.replace_all:
            if edit.operation != "replace":
                raise ValueError("批量匹配只支持替换")
            starts = (
                match.start() for match in re.compile(re.escape(edit.search)).finditer(text, start, end)
            )
        else:
            if text.find(edit.search, first + 1, end) >= 0:
                raise ValueError(f"原文 {edit.search[:80]!r} 匹配多处，请限定段落或加入相邻原文")
            starts = (first,)
        count = 0
        before = edit.search if edit.operation == "replace" else ""
        for left in starts:
            right = left + len(edit.search)
            if edit.operation == "insert_before":
                right = left
            elif edit.operation == "insert_after":
                left = right
            if before != edit.replacement:
                spans.append((left, right, edit.replacement))
                count += 1
        if count:
            changes.append(
                ProseChange(paragraph=edit.paragraph, before=before, after=edit.replacement, count=count)
            )
    result = []
    cursor = 0
    previous_start = -1
    for start, end, replacement in sorted(spans):
        if start < cursor or start == previous_start:
            raise ValueError("同批编辑的范围重叠，请合并成一条替换后重试")
        result.extend((text[cursor:start], replacement))
        cursor, previous_start = end, start
    result.append(text[cursor:])
    candidate = "".join(result)
    if not candidate.strip():
        raise ValueError("修改会清空正文，请使用正文页的回退操作")
    return candidate, changes


def read_prose(text: str, start: int, count: int | Literal["all"] | None, query: str) -> dict:
    requested = MAX_PAGE_SIZE if count == "all" else DEFAULT_PAGE_SIZE if count is None else count
    page_size = max(1, min(requested, MAX_PAGE_SIZE))
    ranges = paragraph_ranges(text)
    matches = []
    for index, (left, right) in enumerate(ranges, start=1):
        if query:
            match = text.find(query, left, right)
            if match < 0:
                continue
        else:
            match = left
        matches.append((index, left, right, match))
    selected = matches[start - 1 : start - 1 + page_size]
    excerpts = []
    for index, left, right, match in selected:
        excerpt_start = max(left, match - 180)
        excerpt_end = min(right, match + len(query) + 180) if query else right
        excerpt = {
            "paragraph": index,
            "text": text[excerpt_start:excerpt_end],
            "truncated": excerpt_start != left or excerpt_end != right,
        }
        if not query and index < len(ranges):
            excerpt["separator_after"] = text[right : ranges[index][0]]
        excerpts.append(excerpt)
    return {
        "revision": prose_revision(text),
        "paragraph_count": len(ranges),
        "matches": len(matches),
        "next_start": start + page_size if start + page_size <= len(matches) else None,
        "paragraphs": excerpts,
    }
