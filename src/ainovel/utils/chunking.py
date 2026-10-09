"""Pure paragraph-safe chunking with sentence overlap metadata."""

import re
from uuid import UUID

from ainovel.core.domain import ContextChunk, Story


def sentences(paragraph: str) -> list[str]:
    return [part.strip() for part in re.findall(r"[^。！？.!?]+[。！？.!?]*", paragraph) if part.strip()]


class ParagraphChunker:
    def __init__(self, target: int = 1500) -> None:
        self.target = target

    def split(self, text: str, chapter_id: UUID, scene_id: UUID, start: int = 0) -> list[ContextChunk]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        groups: list[list[str]] = []
        current: list[str] = []
        length = 0
        for paragraph in paragraphs:
            if current and length + len(paragraph) + 2 > self.target:
                groups.append(current)
                current = []
                length = 0
            current.append(paragraph)
            length += len(paragraph) + 2
        if current:
            groups.append(current)
        chunks: list[ContextChunk] = []
        for index, group in enumerate(groups):
            previous = sentences(groups[index - 1][-1])[-1] if index > 0 else ""
            following = sentences(groups[index + 1][0])[0] if index + 1 < len(groups) else ""
            chunks.append(
                ContextChunk(
                    chapter_id=chapter_id,
                    scene_id=scene_id,
                    ordinal=start + index,
                    kind="paragraph",
                    content="\n\n".join(group),
                    previous_context=previous,
                    next_context=following,
                )
            )
        return chunks

    def story_chunks(self, story: Story) -> list[ContextChunk]:
        chunks: list[ContextChunk] = []
        for scene in story.scenes:
            chunks.extend(self.split(scene.text, scene.chapter_id, scene.id, len(chunks)))
        for chapter in story.chapters:
            if chapter.summary:
                order = next((i for i, scene in enumerate(story.scenes) if scene.chapter_id == chapter.id), 0)
                chunks.append(
                    ContextChunk(
                        chapter_id=chapter.id, ordinal=order, kind="chapter", content=chapter.summary
                    )
                )
        return chunks
