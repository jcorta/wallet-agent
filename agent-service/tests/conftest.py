import asyncio
import json
import sys
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent_service.main import create_app
from agent_service.settings import Settings
from tests.fakes import ScriptedChatModel

# psycopg's async driver cannot run on the Windows default (Proactor) event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
KID = "test-key"
ISSUER = "test-issuer"
AUDIENCE = "test-audience"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


def make_token(sub: str = ALICE, ttl: int = 900) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, _key, algorithm="RS256", headers={"kid": KID})


def auth(sub: str = ALICE, ttl: int = 900) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub, ttl)}"}


CONTACTS = [
    {"id": "c-perez", "alias": "juan", "name": "Juan Pérez"},
    {"id": "c-gomez", "alias": "juan.gomez", "name": "Juan Gómez"},
    {"id": "c-maria", "alias": "maria", "name": "María González"},
]


class FakeWallet:
    """Stands in for wallet-api and records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.transfer_error: str | None = None

    @property
    def transfers(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/transfers"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": "x", "message": "x"}})
        path = request.url.path
        if path == "/accounts/me/balance":
            body = {"balance": "1234.00", "daily_limit": "200000.00", "spent_today": "0.00"}
            return httpx.Response(200, json=body)
        if path == "/accounts/me/contacts":
            return httpx.Response(200, json=CONTACTS)
        if path == "/transfers":
            if self.transfer_error:
                error = {"code": self.transfer_error, "message": "Daily transfer limit exceeded"}
                return httpx.Response(422, json={"error": error})
            sent = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "id": "t-1",
                    "status": "completed",
                    "amount": sent["amount"],
                    "contact_id": sent["contact_id"],
                    "created_at": "2026-01-01T00:00:00Z",
                },
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})


@pytest.fixture
def wallet() -> FakeWallet:
    return FakeWallet()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        wallet_api_url="http://wallet.test",
        jwks_url="http://unused.invalid/jwks",
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
    )


@pytest.fixture
def make_client(settings: Settings, wallet: FakeWallet) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(script: list[AIMessage], **overrides: Any) -> TestClient:
        app = create_app(
            settings,
            model=ScriptedChatModel(responses=script),
            jwks_fetcher=_jwks,
            wallet_transport=httpx.MockTransport(wallet),
            **overrides,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)
