"""One bounded checkpoint for the latest editable user turn, including tool effects."""

from ainovel.core.studio import StudioNovel, TurnChannel, TurnCheckpoint
from ainovel.core.studio_state import require_editable

STATE_FIELDS = {
    "title",
    "world",
    "characters",
    "creation",
    "template_fields",
    "field_template_name",
    "chapters",
    "scenes",
    "branches",
    "creation_messages",
}


def checkpoint(novel, scene, message_id, *, channel: TurnChannel = "discussion"):
    novel.turn_checkpoint = TurnCheckpoint(
        scene_id=scene.entity.id if scene else "",
        message_id=message_id,
        before=novel.model_dump(mode="json", include=STATE_FIELDS),
        channel=channel,
    )


def restore_turn(novel, scene_id, message_id, *, preserve_state=False, channel: TurnChannel = "discussion"):
    turn = novel.turn_checkpoint
    if novel.deleted:
        raise ValueError("请先恢复小说")
    scene = next((s for s in novel.scenes if s.entity.id == scene_id), None) if scene_id else None
    if channel == "writing":
        if not novel.created or scene is None or not scene.confirmed:
            raise ValueError("请先确认演绎")
        messages = scene.writing_messages
    elif novel.created:
        if scene is None:
            raise ValueError("已创建的小说不能回退构思会话")
        require_editable(novel, scene)
        messages = scene.messages
    else:
        if scene_id:
            raise ValueError("构思会话不能指定幕")
        messages = novel.creation_messages
    last = next((m for m in reversed(messages) if m.role == "user"), None)
    if last is None or last.id != message_id:
        raise ValueError("只能操作最后一条用户消息")
    content = last.content
    matching = (
        turn and turn.channel == channel and turn.scene_id == scene_id and turn.message_id == message_id
    )
    legacy = (not matching and not last.checkpointed) if channel == "writing" else turn is None
    if legacy and preserve_state:
        # No historical state is available. This explicitly acknowledged operation
        # changes only the last exchange and never guesses or undoes field effects.
        index = next(i for i, m in enumerate(messages) if m.id == message_id)
        del messages[index:]
        novel.error = ""
        return content
    if not matching:
        if legacy:
            raise ValueError("此旧消息没有回退记录；确认保留当前资料后才能操作消息")
        raise ValueError("本轮的回退记录已被之后的操作替换")
    if turn.version != novel.version:
        raise ValueError("本轮之后资料已发生修改，不能覆盖这些修改；请发送新消息或创建分支")
    candidate = StudioNovel.model_validate(
        {**novel.model_dump(), **turn.before, "turn_checkpoint": None, "error": ""}
    )
    for key in STATE_FIELDS:
        setattr(novel, key, getattr(candidate, key))
    novel.turn_checkpoint, novel.error = None, ""
    return content
