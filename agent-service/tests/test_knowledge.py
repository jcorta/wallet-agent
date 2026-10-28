import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from langchain_core.embeddings.fake import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent_service.knowledge import Chunk, KnowledgeBase, Passage, load_chunks
from tests.conftest import auth
from tests.fakes import tool_call

KNOWLEDGE_DIR = Path(__file__).parents[1] / "knowledge"
ADMIN_URL = os.environ.get("TEST_DATABASE_URL")


def test_documents_are_split_by_section() -> None:
    chunks = load_chunks(KNOWLEDGE_DIR)
    assert {c.source for c in chunks} == {"acreditacion.md", "comisiones.md", "limites.md"}
    limit = next(c for c in chunks if "límite diario de transferencias" in c.content.lower())
    assert limit.content.startswith("Límites de la cuenta - Límite diario")
    assert "$200.000" in limit.content


class CountingEmbeddings(DeterministicFakeEmbedding):
    embedded: int = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return super().embed_documents(texts)


@pytest_asyncio.fixture
async def base() -> AsyncIterator[tuple[KnowledgeBase, CountingEmbeddings]]:
    if ADMIN_URL is None:
        pytest.skip("TEST_DATABASE_URL is not set")
    url = ADMIN_URL.replace("postgresql+psycopg", "postgresql")
    schema = f"knowledge_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(f"CREATE SCHEMA {schema}")
    pool = AsyncConnectionPool(
        f"{url}?options=-csearch_path%3D{schema}%2Cpublic",
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    embeddings = CountingEmbeddings(size=16)
    knowledge = KnowledgeBase(pool, embeddings, "fake:16")
    await knowledge.setup()
    yield knowledge, embeddings
    await pool.close()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.mark.asyncio
async def test_search_returns_the_closest_chunk(
    base: tuple[KnowledgeBase, CountingEmbeddings],
) -> None:
    knowledge, _ = base
    chunks = load_chunks(KNOWLEDGE_DIR)
    await knowledge.sync(chunks)
    target = chunks[3]
    found = await knowledge.search(target.content, limit=2)
    assert found[0] == Passage(target.source, target.content)
    assert len(found) == 2


@pytest.mark.asyncio
async def test_sync_embeds_only_what_changed(
    base: tuple[KnowledgeBase, CountingEmbeddings],
) -> None:
    knowledge, embeddings = base
    chunks = [Chunk("a.md", "uno"), Chunk("b.md", "dos")]
    await knowledge.sync(chunks)
    await knowledge.sync(chunks)
    assert embeddings.embedded == 2

    await knowledge.sync([chunks[0], Chunk("b.md", "dos editado")])
    assert embeddings.embedded == 3
    contents = {p.content for p in await knowledge.search("x", limit=10)}
    assert contents == {"uno", "dos editado"}


def test_agent_answers_from_retrieved_passages(make_client) -> None:  # type: ignore[no-untyped-def]
    class Stub:
        queries: list[str] = []

        async def search(self, query: str, limit: int = 3) -> list[Passage]:
            self.queries.append(query)
            return [Passage("limites.md", "El límite diario es de $200.000")]

    stub = Stub()
    client = make_client(
        [tool_call("search_knowledge", {"query": "límite diario"}), AIMessage("Es de $200.000")],
        knowledge=stub,
    )
    response = client.post("/chat", json={"message": "¿Cuál es el límite?"}, headers=auth())
    assert response.json()["reply"] == "Es de $200.000"
    assert stub.queries == ["límite diario"]

    graph = client.app.state.graph  # type: ignore[attr-defined]
    state = graph.get_state({"configurable": {"thread_id": response.json()["thread_id"]}})
    tool_result = json.loads(next(m for m in state.values["messages"] if m.type == "tool").content)
    assert tool_result == [{"source": "limites.md", "content": "El límite diario es de $200.000"}]
