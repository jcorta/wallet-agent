import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request, Response
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from wallet_api import service
from wallet_api.auth import JwksCache, current_user_id, http_jwks_fetcher
from wallet_api.errors import ApiError, ErrorBody, install_error_handlers
from wallet_api.logging import RequestIdMiddleware, configure_logging
from wallet_api.models import Base
from wallet_api.schemas import (
    BalanceResponse,
    ContactResponse,
    HealthResponse,
    TransactionResponse,
    TransferRequest,
    TransferResponse,
)
from wallet_api.seed import seed
from wallet_api.settings import Settings

logger = logging.getLogger(__name__)

AUTH_ERRORS: dict[int | str, dict[str, Any]] = {401: {"model": ErrorBody}}


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
UserDep = Annotated[str, Depends(current_user_id)]


def create_app(
    settings: Settings | None = None,
    jwks_fetcher: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        Base.metadata.create_all(engine)
        app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)
        with app.state.sessionmaker() as session:
            seed(session, settings)
        yield
        engine.dispose()

    app = FastAPI(title="wallet-api", lifespan=lifespan)
    app.state.settings = settings
    fetch = jwks_fetcher or http_jwks_fetcher(settings.jwks_url, settings.http_timeout_seconds)
    app.state.jwks = JwksCache(fetch, settings.jwks_cache_ttl_seconds)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.get("/accounts/me/balance", response_model=BalanceResponse, responses=AUTH_ERRORS)
    def balance(user_id: UserDep, session: SessionDep) -> BalanceResponse:
        account = service.get_account(session, user_id)
        return BalanceResponse(
            balance=account.balance,
            daily_limit=account.daily_limit,
            spent_today=service.spent_today(session, user_id, settings.timezone),
        )

    @app.get(
        "/accounts/me/transactions",
        response_model=list[TransactionResponse],
        responses=AUTH_ERRORS,
    )
    def transactions(
        user_id: UserDep,
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> list[TransactionResponse]:
        return [
            TransactionResponse(
                id=transfer.id,
                direction=direction,  # type: ignore[arg-type]
                status=transfer.status,  # type: ignore[arg-type]
                amount=transfer.amount,
                counterparty_name=counterparty,
                rejection_reason=transfer.rejection_reason,
                created_at=transfer.created_at,
            )
            for transfer, direction, counterparty in service.list_transactions(
                session, user_id, limit
            )
        ]

    @app.get("/accounts/me/contacts", response_model=list[ContactResponse], responses=AUTH_ERRORS)
    def contacts(user_id: UserDep, session: SessionDep) -> list[ContactResponse]:
        return [
            ContactResponse(id=c.id, alias=c.alias, name=c.name)
            for c in service.list_contacts(session, user_id)
        ]

    @app.post(
        "/transfers",
        response_model=TransferResponse,
        status_code=201,
        responses={**AUTH_ERRORS, 409: {"model": ErrorBody}, 422: {"model": ErrorBody}},
    )
    def create_transfer(
        body: TransferRequest, user_id: UserDep, session: SessionDep, response: Response
    ) -> TransferResponse:
        transfer, replayed = service.execute_transfer(session, user_id, body, settings.timezone)
        logger.info(
            "transfer processed",
            extra={
                "fields": {
                    "user_id": user_id,
                    "transfer_id": str(transfer.id),
                    "status": transfer.status,
                    "reason": transfer.rejection_reason,
                    "replayed": replayed,
                }
            },
        )
        if transfer.status == service.REJECTED:
            raise service.rejection_error(transfer)
        if replayed:
            response.status_code = 200
        return TransferResponse(
            id=transfer.id,
            status="completed",
            amount=transfer.amount,
            contact_id=transfer.contact_id,
            created_at=transfer.created_at,
        )

    @app.get("/health", response_model=HealthResponse)
    def health(session: SessionDep) -> HealthResponse:
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise ApiError(503, "database_unavailable", "Database is not reachable") from exc
        return HealthResponse()

    return app


def get_app() -> FastAPI:
    return create_app()
