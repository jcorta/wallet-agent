import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Request
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field, SecretStr, StrictBool

from agent_service.auth import JwksCache, current_user_id, http_jwks_fetcher
from agent_service.errors import ApiError, ErrorBody, install_error_handlers
from agent_service.graph import build_graph
from agent_service.llm import build_chat_model
from agent_service.logging import RequestIdMiddleware, configure_logging
from agent_service.settings import Settings
from agent_service.threads import InMemoryThreadStore, PostgresThreadStore, ThreadStore
from agent_service.wallet_client import UpstreamError, WalletClient

logger = logging.getLogger(__name__)

UserDep = Annotated[str, Depends(current_user_id)]

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorBody} for code in (401, 404, 409, 502)
}


class ChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class ConfirmRequest(BaseModel):
    approve: StrictBool


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "pending_confirmation"]
    reply: str | None = None
    summary: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@asynccontextmanager
async def _persistence(
    settings: Settings,
) -> AsyncIterator[tuple[BaseCheckpointSaver, ThreadStore]]:
    if settings.database_url is None:
        yield InMemorySaver(), InMemoryThreadStore()
        return
    pool = AsyncConnectionPool(
        settings.database_url,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    try:
        checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await checkpointer.setup()
        threads = PostgresThreadStore(pool)
        await threads.setup()
        yield checkpointer, threads
    finally:
        await pool.close()


def _pending_summary(snapshot: Any) -> str | None:
    return snapshot.interrupts[0].value["summary"] if snapshot.interrupts else None


def create_app(
    settings: Settings | None = None,
    *,
    model: BaseChatModel | None = None,
    jwks_fetcher: Callable[[], dict[str, Any]] | None = None,
    wallet_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        wallet = WalletClient(
            settings.wallet_api_url, settings.http_timeout_seconds, wallet_transport
        )
        async with _persistence(settings) as (checkpointer, threads):
            app.state.threads = threads
            app.state.graph = build_graph(model or build_chat_model(settings), wallet, checkpointer)
            yield
        await wallet.aclose()

    app = FastAPI(title="agent-service", lifespan=lifespan)
    app.state.settings = settings
    fetch = jwks_fetcher or http_jwks_fetcher(settings.jwks_url, settings.http_timeout_seconds)
    app.state.jwks = JwksCache(fetch, settings.jwks_cache_ttl_seconds)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    async def require_owned_thread(request: Request, thread_id: str, user_id: str) -> None:
        # Foreign and unknown threads are indistinguishable, so ids cannot be probed.
        owner = await request.app.state.threads.owner(thread_id)
        if owner != user_id:
            raise ApiError(404, "thread_not_found", "Conversation not found")

    async def pending_summary(request: Request, thread_id: str) -> str | None:
        graph: CompiledStateGraph = request.app.state.graph
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return _pending_summary(snapshot)

    async def run(request: Request, thread_id: str, graph_input: Any) -> ChatResponse:
        graph: CompiledStateGraph = request.app.state.graph
        token = request.headers["authorization"].split(" ", 1)[1]
        config = {
            "configurable": {
                "thread_id": thread_id,
                "access_token": SecretStr(token),
                "request_id": request.state.request_id,
            }
        }
        try:
            await graph.ainvoke(graph_input, config)
        except UpstreamError as exc:
            if exc.token_rejected:
                raise ApiError(401, "invalid_token", "Invalid or expired token") from exc
            raise ApiError(502, "upstream_unavailable", "A backend service is unavailable") from exc

        snapshot = await graph.aget_state(config)
        summary = _pending_summary(snapshot)
        if summary is not None:
            return ChatResponse(thread_id=thread_id, status="pending_confirmation", summary=summary)
        reply = next(m for m in reversed(snapshot.values["messages"]) if isinstance(m, AIMessage))
        return ChatResponse(thread_id=thread_id, status="completed", reply=reply.text)

    @app.post("/chat", response_model=ChatResponse, responses=ERROR_RESPONSES)
    async def chat(body: ChatRequest, request: Request, user_id: UserDep) -> ChatResponse:
        if body.thread_id is None:
            thread_id = uuid.uuid4().hex
            await request.app.state.threads.create(thread_id, user_id)
        else:
            thread_id = body.thread_id
            await require_owned_thread(request, thread_id, user_id)
            if await pending_summary(request, thread_id) is not None:
                raise ApiError(
                    409, "confirmation_pending", "Confirm or cancel the pending transfer first"
                )
        return await run(request, thread_id, {"messages": [HumanMessage(body.message)]})

    @app.post("/chat/{thread_id}/confirm", response_model=ChatResponse, responses=ERROR_RESPONSES)
    async def confirm(
        thread_id: str, body: ConfirmRequest, request: Request, user_id: UserDep
    ) -> ChatResponse:
        await require_owned_thread(request, thread_id, user_id)
        if await pending_summary(request, thread_id) is None:
            raise ApiError(409, "nothing_to_confirm", "No transfer is waiting for confirmation")
        logger.info(
            "transfer decision",
            extra={"fields": {"user_id": user_id, "thread_id": thread_id, "approve": body.approve}},
        )
        return await run(request, thread_id, Command(resume=body.approve))

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


def get_app() -> FastAPI:
    return create_app()
