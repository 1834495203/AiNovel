"""Offline embedding adapter for development and local retrieval."""

import hashlib
import math
from uuid import UUID

import jieba


class HashEmbedder:
    """Deterministic offline demo embedding. Replace with semantic embeddings for actual writing."""

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str], command_id: UUID) -> list[list[float]]:
        result: list[list[float]] = []
        for content in texts:
            vector = [0.0] * self.dimensions
            for token in jieba.lcut(content):
                digest = hashlib.sha256(token.encode()).digest()
                index = int.from_bytes(digest[:4]) % self.dimensions
                vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            result.append([value / norm for value in vector])
        return result
