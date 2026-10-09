"""Session/history projections derived from the reversible command journal."""

from uuid import UUID

from ainovel.core.discussion import (
    DiscussionRequest,
    DiscussionSession,
    DiscussionTurn,
    StartDiscussionRequest,
)
from ainovel.core.domain import CommandView
from ainovel.mapper.store import Store


class DiscussionRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def sessions(self, story_id: UUID) -> list[DiscussionSession]:
        commands = await self.store.commands(story_id)
        result: list[DiscussionSession] = []
        for command in commands:
            payload = command.submission.payload
            if (
                not isinstance(payload, StartDiscussionRequest)
                or command.undone
                or command.status != "completed"
            ):
                continue
            session = command.output.discussion_session
            if session is None:
                continue
            view = session.model_copy(deep=True)
            messages = [
                item
                for item in commands
                if isinstance(item.submission.payload, DiscussionRequest)
                and item.submission.payload.session_id == session.id
                and not item.undone
            ]
            view.message_count = sum(item.status == "completed" for item in messages)
            if messages:
                view.updated_at = messages[-1].created_at
                if view.title == "新讨论":
                    view.title = messages[0].submission.payload.message[:28]
            result.append(view)
        return sorted(result, key=lambda item: item.updated_at, reverse=True)

    async def commands(self, story_id: UUID, session_id: UUID) -> list[CommandView]:
        if not any(item.id == session_id for item in await self.sessions(story_id)):
            raise ValueError("讨论会话不存在或已撤回")
        return [
            item
            for item in await self.store.commands(story_id)
            if isinstance(item.submission.payload, DiscussionRequest)
            and item.submission.payload.session_id == session_id
            and not item.undone
        ]

    async def history(self, story_id: UUID, session_id: UUID) -> list[DiscussionTurn]:
        result: list[DiscussionTurn] = []
        for item in (await self.commands(story_id, session_id))[-20:]:
            if item.status == "completed" and item.output.discussion:
                result.extend(
                    [
                        DiscussionTurn(role="user", content=item.submission.payload.message),
                        DiscussionTurn(role="assistant", content=item.output.discussion.reply),
                    ]
                )
        return result
