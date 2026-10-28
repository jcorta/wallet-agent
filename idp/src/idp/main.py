import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from pydantic import BaseModel

from idp.errors import ApiError, ErrorBody, install_error_handlers
from idp.keys import KeyStore
from idp.logging import RequestIdMiddleware, configure_logging
from idp.settings import Settings

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.keys = KeyStore(settings.key_path)
        yield

    app = FastAPI(title="idp", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.post("/login", response_model=TokenResponse, responses={401: {"model": ErrorBody}})
    async def login(body: LoginRequest, request: Request) -> TokenResponse:
        user = next((u for u in settings.users if u.username == body.username), None)
        # Compare against an empty password when the user is unknown so timing is uniform.
        expected = user.password if user else ""
        valid = hmac.compare_digest(expected.encode(), body.password.encode())
        if user is None or not valid:
            logger.warning("login rejected")
            raise ApiError(401, "invalid_credentials", "Invalid username or password")

        now = int(time.time())
        token = request.app.state.keys.sign(
            {
                "sub": user.user_id,
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": now,
                "exp": now + settings.token_ttl_seconds,
            }
        )
        logger.info("login succeeded", extra={"fields": {"user_id": user.user_id}})
        return TokenResponse(access_token=token, expires_in=settings.token_ttl_seconds)

    @app.get("/.well-known/jwks.json")
    async def jwks(request: Request) -> dict[str, list[dict[str, object]]]:
        return request.app.state.keys.jwks()

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


def get_app() -> FastAPI:
    return create_app()
