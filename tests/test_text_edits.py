import pytest
from pydantic import ValidationError

from ainovel.core.text_edits import TextEdit, apply_text_edits


def edit(original: str, replacement: str) -> TextEdit:
    return TextEdit(original=original, replacement=replacement, reason="修改用户指定的表达")


def test_disjoint_edits_use_original_coordinates_and_preserve_spacing() -> None:
    source = "开头不变。\n\n她很紧张。她轻声说话。\n\n尾声不变。"
    assert apply_text_edits(
        source, [edit("她轻声说话。", "她抬头。"), edit("她很紧张。", "她攥住袖口。")]
    ) == ("开头不变。\n\n她攥住袖口。她抬头。\n\n尾声不变。")


@pytest.mark.parametrize("source, anchor", [("她说。她说。", "她说。"), ("aaa", "aa")])
def test_ambiguous_anchor_never_replaces_all_occurrences(source: str, anchor: str) -> None:
    with pytest.raises(ValueError, match="重复"):
        apply_text_edits(source, [edit(anchor, "新句")])


def test_context_disambiguates_repeated_sentences() -> None:
    source = "甲：她说。乙：她说。"
    assert apply_text_edits(source, [edit("乙：她说。", "乙：她沉默。")]) == "甲：她说。乙：她沉默。"


def test_missing_anchor_and_overlap_are_rejected_atomically() -> None:
    source = "第一句。第二句。第三句。"
    with pytest.raises(ValueError, match="不一致"):
        apply_text_edits(source, [edit("第一句。", "改了。"), edit("不存在。", "新句。")])
    with pytest.raises(ValueError, match="重叠"):
        apply_text_edits(source, [edit("第一句。第二句。", "新句。"), edit("第二句。", "改了。")])
    assert source == "第一句。第二句。第三句。"


def test_deletion_insertion_and_no_op() -> None:
    source = "开头。中间。结尾。"
    assert (
        apply_text_edits(source, [edit("中间。", ""), edit("结尾。", "新句。结尾。")]) == "开头。新句。结尾。"
    )
    assert apply_text_edits(source, []) == source
    with pytest.raises(ValidationError):
        edit("", "新句。")
