"""Project-wide command journal. Reads projections, never checkpoint payloads."""

from datetime import datetime
from uuid import UUID

from pydantic import Field
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from ainovel.core.base import Model
from ainovel.core.domain import CommandView
from ainovel.mapper.models import CommandRow, OperationRow, StoryRow
from ainovel.mapper.store import Store


class HistoryItem(Model):
    command: CommandView
    created_at: datetime
    scope_id: UUID
    story_title: str = "创建访谈 / 全局设置"
    operation_id: int | None = None


class HistoryTarget(Model):
    command_id: UUID
    operation_id: int
    scope_id: UUID
    story_id: UUID | None = None
    revision_id: UUID | None = None


class HistoryPage(Model):
    items: list[HistoryItem] = Field(default_factory=list)
    latest: HistoryTarget | None = None
    latest_command_id: UUID | None = None
    total: int = 0


async def project_history(store: Store, scope_id: UUID | None, offset: int, limit: int) -> HistoryPage:
    async with store.sessions() as session:
        query = store.command_projection_query()
        count_query = select(func.count()).select_from(CommandRow)
        # Session IDs live in a typed submission; SQL filters their JSON wire representation.
        if scope_id is not None:
            condition = or_(
                CommandRow.story_id == scope_id,
                cast(CommandRow.output, JSONB)["story_id"].astext == str(scope_id),
                cast(CommandRow.payload, JSONB)["session_id"].astext == str(scope_id),
            )
            query = query.where(condition)
            count_query = count_query.where(condition)
        kind = cast(CommandRow.payload, JSONB)["payload"]["kind"].astext
        latest_command_id = await session.scalar(
            query.with_only_columns(CommandRow.id)
            .where(
                CommandRow.undone_at.is_(None), kind.not_in(("undo", "save_setup_draft", "recover_command"))
            )
            .order_by(CommandRow.created_at.desc(), CommandRow.id.desc())
            .limit(1)
        )
        rows = (
            await session.execute(
                query.order_by(CommandRow.created_at.desc(), CommandRow.id.desc()).offset(offset).limit(limit)
            )
        ).all()
        total = int(await session.scalar(count_query) or 0)
        operation_query = select(OperationRow.id, OperationRow.command_id, OperationRow.scope_id).where(
            OperationRow.undone_by.is_(None)
        )
        if scope_id is not None:
            operation_query = operation_query.where(OperationRow.scope_id == scope_id)
        top = (await session.execute(operation_query.order_by(OperationRow.id.desc()).limit(1))).first()
        titles = (await session.execute(select(StoryRow.id, StoryRow.title))).all()
        items: list[HistoryItem] = []
        for row in rows:
            command = store.projected_view(store.command_projection(row))
            story_id = command.submission.story_id or command.output.story_id
            scope = story_id or command.submission.session_id or command.submission.id
            items.append(
                HistoryItem(
                    command=command,
                    created_at=row.created_at,
                    scope_id=scope,
                    story_title=next(
                        (title for book_id, title in titles if book_id == story_id),
                        "创建访谈 / 全局设置",
                    ),
                    operation_id=top.id if top and command.submission.id == top.command_id else None,
                )
            )
        latest = None
        if top:
            target = await store.command(top.command_id)
            story_id = target.submission.story_id or target.output.story_id
            story = await session.get(StoryRow, story_id) if story_id else None
            latest = HistoryTarget(
                command_id=top.command_id,
                operation_id=top.id,
                scope_id=top.scope_id,
                story_id=story_id,
                revision_id=story.head_id if story else None,
            )
        return HistoryPage(items=items, latest=latest, latest_command_id=latest_command_id, total=total)
