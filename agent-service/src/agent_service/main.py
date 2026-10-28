import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Request
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field, SecretStr

from agent_service.auth import JwksCache, current_user_id, http_jwks_fetcher
from agent_service.errors import ApiError, ErrorBody, install_error_handlers
from agent_service.graph import build_graph
from agent_service.llm import build_chat_model
from agent_service.logging import RequestIdMiddleware, configure_logging
from agent_service.settings import Settings
from agent_service.tools import build_read_tools
from agent_service.wallet_client import UpstreamError, WalletClient

logger = logging.getLogger(__name__)

UserDep = Annotated[str, Depends(current_user_id)]


class ChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed"]
    reply: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


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
        app.state.graph = build_graph(
            model or build_chat_model(settings), build_read_tools(wallet), InMemorySaver()
        )
        yield
        await wallet.aclose()

    app = FastAPI(title="agent-service", lifespan=lifespan)
    app.state.settings = settings
    fetch = jwks_fetcher or http_jwks_fetcher(settings.jwks_url, settings.http_timeout_seconds)
    app.state.jwks = JwksCache(fetch, settings.jwks_cache_ttl_seconds)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={401: {"model": ErrorBody}, 502: {"model": ErrorBody}},
    )
    async def chat(body: ChatRequest, request: Request, user: UserDep) -> ChatResponse:
        thread_id = body.thread_id or uuid.uuid4().hex
        token = request.headers["authorization"].split(" ", 1)[1]
        config = {
            "configurable": {
                "thread_id": thread_id,
                "access_token": SecretStr(token),
                "request_id": request.state.request_id,
            }
        }
        try:
            result = await request.app.state.graph.ainvoke(
                {"messages": [HumanMessage(body.message)]}, config
            )
        except UpstreamError as exc:
            if exc.token_rejected:
                raise ApiError(401, "invalid_token", "Invalid or expired token") from exc
            raise ApiError(502, "upstream_unavailable", "A backend service is unavailable") from exc
        reply = next(m for m in reversed(result["messages"]) if isinstance(m, AIMessage))
        return ChatResponse(thread_id=thread_id, status="completed", reply=reply.text)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


def get_app() -> FastAPI:
    return create_app()
