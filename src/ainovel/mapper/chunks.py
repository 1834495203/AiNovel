"""Vector SQL stays in the persistence layer and returns validated domain chunks."""

from uuid import UUID

from sqlalchemy import select

from ainovel.core.retrieval import RecallCandidates
from ainovel.mapper.models import ChunkRow
from ainovel.mapper.store import Store


class ChunkMapper:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def recall(self, revision_id: UUID, vector: list[float]) -> RecallCandidates:
        async with self.store.sessions() as session:
            base = select(ChunkRow).where(ChunkRow.revision_id == revision_id)
            distance = ChunkRow.embedding.cosine_distance(vector)
            chapters = list(
                (
                    await session.scalars(base.where(ChunkRow.kind == "chapter").order_by(distance).limit(3))
                ).all()
            )
            chapter_ids = [row.chapter_id for row in chapters]
            paragraphs = list(
                (
                    await session.scalars(
                        base.where(ChunkRow.kind == "paragraph", ChunkRow.chapter_id.in_(chapter_ids))
                        .order_by(distance)
                        .limit(6)
                    )
                ).all()
            )
            global_hits = list((await session.scalars(base.order_by(distance).limit(8))).all())
            return RecallCandidates(
                chapters=[row.payload for row in chapters],
                paragraphs=[row.payload for row in paragraphs],
                global_hits=[row.payload for row in global_hits],
            )
