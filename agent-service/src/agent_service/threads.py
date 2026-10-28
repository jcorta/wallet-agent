from typing import Protocol

from psycopg_pool import AsyncConnectionPool


class ThreadStore(Protocol):
    async def setup(self) -> None: ...

    async def create(self, thread_id: str, user_id: str) -> None: ...

    async def owner(self, thread_id: str) -> str | None: ...


class InMemoryThreadStore:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    async def setup(self) -> None:
        return None

    async def create(self, thread_id: str, user_id: str) -> None:
        self._owners[thread_id] = user_id

    async def owner(self, thread_id: str) -> str | None:
        return self._owners.get(thread_id)


class PostgresThreadStore:
    """Records which user created each thread; the checkpointer itself knows nothing of users."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id  text PRIMARY KEY,
                    user_id    text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )

    async def create(self, thread_id: str, user_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO threads (thread_id, user_id) VALUES (%s, %s)", (thread_id, user_id)
            )

    async def owner(self, thread_id: str) -> str | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT user_id FROM threads WHERE thread_id = %s", (thread_id,)
            )
            row = await cursor.fetchone()
        return row["user_id"] if row else None
