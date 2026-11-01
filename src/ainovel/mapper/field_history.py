"""Append-only journal and sparse snapshots committed with the story revision."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ainovel.core.domain import Revision, Story
from ainovel.core.field_history import FieldChangeEvent, FieldSnapshot, apply_patches, diff_values
from ainovel.mapper.models import FieldChangeRow, FieldSnapshotRow, RevisionRow

SNAPSHOT_INTERVAL = 20


async def restore(session: AsyncSession, revision_id: UUID, story_id: UUID) -> Story:
    cursor = revision_id
    events: list[FieldChangeEvent] = []
    seen: set[UUID] = set()
    while True:
        if cursor in seen:
            raise ValueError("字段历史存在循环")
        seen.add(cursor)
        snapshot = await session.get(FieldSnapshotRow, cursor)
        if snapshot:
            if snapshot.story_id != story_id:
                raise ValueError("快照不属于当前小说")
            document = snapshot.payload.document
            break
        event = await session.get(FieldChangeRow, cursor)
        if event is None:
            # Old revisions remain immutable baseline snapshots; no destructive migration.
            legacy = await session.get(RevisionRow, cursor)
            if legacy is None or legacy.story_id != story_id:
                raise ValueError("缺少字段历史基线")
            document = legacy.payload.story.model_dump(mode="json", round_trip=True)
            break
        if event.story_id != story_id:
            raise ValueError("字段事件不属于当前小说")
        events.append(event.payload)
        if event.parent_id is None:
            document = None
            break
        cursor = event.parent_id
    for event in reversed(events):
        document = apply_patches(document, event.patches)
    return Story.model_validate(document)


async def append(session: AsyncSession, revision: Revision) -> None:
    before = None
    depth = 0
    if revision.parent_id:
        parent = await restore(session, revision.parent_id, revision.story_id)
        before = parent.model_dump(mode="json", round_trip=True)
        parent_event = await session.get(FieldChangeRow, revision.parent_id)
        depth = parent_event.payload.depth + 1 if parent_event else 1
        # Save a baseline for the first legacy parent without rewriting its data.
        if parent_event is None and await session.get(FieldSnapshotRow, revision.parent_id) is None:
            session.add(
                FieldSnapshotRow(
                    revision_id=revision.parent_id,
                    story_id=revision.story_id,
                    payload=FieldSnapshot(
                        revision_id=revision.parent_id, story_id=revision.story_id, document=before
                    ),
                )
            )
    document = revision.story.model_dump(mode="json", round_trip=True)
    event = FieldChangeEvent(
        revision_id=revision.id,
        story_id=revision.story_id,
        parent_id=revision.parent_id,
        command_id=revision.command_id,
        reason=revision.reason,
        depth=depth,
        patches=diff_values(before, document),
        created_at=revision.created_at,
    )
    session.add(
        FieldChangeRow(
            revision_id=revision.id, story_id=revision.story_id, parent_id=revision.parent_id, payload=event
        )
    )
    if depth % SNAPSHOT_INTERVAL == 0:
        session.add(
            FieldSnapshotRow(
                revision_id=revision.id,
                story_id=revision.story_id,
                payload=FieldSnapshot(revision_id=revision.id, story_id=revision.story_id, document=document),
            )
        )


async def history(
    session: AsyncSession, story_id: UUID, revision_id: UUID, limit: int = 50
) -> list[FieldChangeEvent]:
    """Follow ancestry, never combine events from siblings. Cursor is a revision ID."""
    result = []
    cursor = revision_id
    while cursor and len(result) < limit:
        revision = await session.get(RevisionRow, cursor)
        if revision is None or revision.story_id != story_id:
            raise ValueError("历史版本不属于当前小说")
        row = await session.get(FieldChangeRow, cursor)
        if row:
            result.append(row.payload)
        cursor = revision.payload.parent_id
    return result
