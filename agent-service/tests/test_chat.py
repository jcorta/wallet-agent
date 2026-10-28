from collections.abc import Callable

import jwt
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import ALICE, FakeWallet, auth, make_token
from tests.fakes import tool_call

Factory = Callable[[list[AIMessage]], TestClient]


def test_balance_question_calls_wallet_with_user_token(
    make_client: Factory, wallet: FakeWallet
) -> None:
    client = make_client([tool_call("get_balance"), AIMessage("Tenés $1.234,00")])
    response = client.post(
        "/chat", json={"message": "¿Cuánto tengo?"}, headers={"X-Request-ID": "rid-1", **auth()}
    )
    assert response.status_code == 200
    assert response.json()["reply"] == "Tenés $1.234,00"
    assert response.json()["status"] == "completed"

    (call,) = wallet.requests
    forwarded = call.headers["authorization"].removeprefix("Bearer ")
    assert jwt.decode(forwarded, options={"verify_signature": False})["sub"] == ALICE
    assert call.headers["x-request-id"] == "rid-1"


def test_token_never_reaches_messages_or_checkpoints(
    make_client: Factory, wallet: FakeWallet
) -> None:
    token = make_token()
    client = make_client([tool_call("get_balance"), AIMessage("ok")])
    thread_id = client.post(
        "/chat",
        json={"message": "saldo"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["thread_id"]

    graph = client.app.state.graph  # type: ignore[attr-defined]
    saved = graph.checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    assert token not in repr(saved.checkpoint) + repr(saved.metadata)
    assert ALICE not in repr(saved.checkpoint["channel_values"])


def test_thread_keeps_conversation(make_client: Factory) -> None:
    client = make_client([AIMessage("Hola"), AIMessage("Seguimos")])
    first = client.post("/chat", json={"message": "hola"}, headers=auth()).json()
    second = client.post(
        "/chat", json={"message": "dale", "thread_id": first["thread_id"]}, headers=auth()
    ).json()
    assert second["thread_id"] == first["thread_id"]
    state = client.app.state.graph.get_state(  # type: ignore[attr-defined]
        {"configurable": {"thread_id": first["thread_id"]}}
    )
    assert len(state.values["messages"]) == 4


def test_chat_requires_a_valid_token(make_client: Factory) -> None:
    client = make_client([AIMessage("hola")])
    assert client.post("/chat", json={"message": "hola"}).status_code == 401
    assert client.post("/chat", json={"message": "hola"}, headers=auth(ttl=-5)).status_code == 401


def test_rejected_user_token_becomes_401(make_client: Factory, wallet: FakeWallet) -> None:
    wallet.status = 401
    client = make_client([tool_call("get_balance")])
    response = client.post("/chat", json={"message": "saldo"}, headers=auth())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_wallet_outage_becomes_502_without_details(
    make_client: Factory, wallet: FakeWallet
) -> None:
    wallet.status = 503
    client = make_client([tool_call("get_balance")])
    response = client.post("/chat", json={"message": "saldo"}, headers=auth())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"


def test_empty_message_is_a_validation_error(make_client: Factory) -> None:
    client = make_client([AIMessage("hola")])
    response = client.post("/chat", json={"message": ""}, headers=auth())
    assert response.status_code == 422
