import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from langchain_core.embeddings import Embeddings
from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class Chunk:
    source: str
    content: str


@dataclass(frozen=True)
class Passage:
    source: str
    content: str


class KnowledgeSearch(Protocol):
    async def search(self, query: str, limit: int = 3) -> list[Passage]: ...


def load_chunks(directory: Path) -> list[Chunk]:
    """One chunk per `## ` section, prefixed with the document title for context."""
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        title, *sections = re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.M)
        heading = title.strip().lstrip("# ").strip()
        for section in sections:
            chunks.append(Chunk(source=path.name, content=f"{heading} - {section.strip()}"))
    return chunks


class KnowledgeBase:
    def __init__(
        self, pool: AsyncConnectionPool, embeddings: Embeddings, embedding_model: str
    ) -> None:
        self._pool = pool
        self._embeddings = embeddings
        self._embedding_model = embedding_model

    def _digest(self, chunk: Chunk) -> str:
        # The model is part of the identity: vectors from another model are not comparable.
        raw = f"{self._embedding_model}\0{chunk.source}\0{chunk.content}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    digest    text PRIMARY KEY,
                    source    text NOT NULL,
                    content   text NOT NULL,
                    embedding vector NOT NULL
                )
                """
            )

    async def sync(self, chunks: list[Chunk]) -> None:
        """Brings the table in line with `chunks`, embedding only what is new."""
        wanted = {self._digest(c): c for c in chunks}
        async with self._pool.connection() as conn:
            cursor = await conn.execute("SELECT digest FROM knowledge_chunks")
            existing = {row["digest"] for row in await cursor.fetchall()}
            await conn.execute(
                "DELETE FROM knowledge_chunks WHERE digest <> ALL(%s)", (list(wanted),)
            )
            new = [(d, c) for d, c in wanted.items() if d not in existing]
            if not new:
                return
            vectors = await self._embeddings.aembed_documents([c.content for _, c in new])
            for (digest, chunk), vector in zip(new, vectors, strict=True):
                await conn.execute(
                    "INSERT INTO knowledge_chunks (digest, source, content, embedding)"
                    " VALUES (%s, %s, %s, %s::vector)",
                    (digest, chunk.source, chunk.content, _literal(vector)),
                )

    async def search(self, query: str, limit: int = 3) -> list[Passage]:
        vector = await self._embeddings.aembed_query(query)
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT source, content FROM knowledge_chunks"
                " ORDER BY embedding <=> %s::vector LIMIT %s",
                (_literal(vector), limit),
            )
            return [Passage(row["source"], row["content"]) for row in await cursor.fetchall()]


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vector) + "]"
