import os
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings.fake import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage

from agent_service.main import create_app
from agent_service.settings import Settings
from tests.conftest import AUDIENCE, BOB, ISSUER, FakeWallet, _jwks, auth
from tests.fakes import ScriptedChatModel, tool_call

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(ADMIN_URL is None, reason="TEST_DATABASE_URL is not set")


def _app(database_url: str, wallet: FakeWallet, script: list[AIMessage]) -> TestClient:
    settings = Settings(
        wallet_api_url="http://wallet.test",
        jwks_url="http://unused.invalid/jwks",
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        database_url=database_url,
    )
    model = ScriptedChatModel(responses=script)
    app = create_app(
        settings,
        model=model,
        jwks_fetcher=_jwks,
        wallet_transport=httpx.MockTransport(wallet),
        embeddings=DeterministicFakeEmbedding(size=8),
    )
    return TestClient(app)


def test_pending_transfer_survives_a_restart_and_keeps_its_owner(wallet: FakeWallet) -> None:
    assert ADMIN_URL is not None
    schema = f"agent_test_{uuid.uuid4().hex[:8]}"
    base = ADMIN_URL.replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(f"CREATE SCHEMA {schema}")
    database_url = f"{base}?options=-csearch_path%3D{schema}%2Cpublic"

    request = tool_call("request_transfer", {"contact": "María", "amount": 50000})
    try:
        with _app(database_url, wallet, [request]) as first:
            pending = first.post("/chat", json={"message": "Mandale 50000 a María"}, headers=auth())
            assert pending.json()["status"] == "pending_confirmation"
            thread_id = pending.json()["thread_id"]

        # A new application instance has no in-memory state: only Postgres remembers.
        with _app(database_url, wallet, [AIMessage("Transferencia hecha")]) as second:
            assert (
                second.post(
                    f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth(BOB)
                ).status_code
                == 404
            )
            assert wallet.transfers == []

            done = second.post(f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth())
            assert done.json()["status"] == "completed"
            assert len(wallet.transfers) == 1
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
