import json
from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from agent_service.transfers import format_ars, resolve_contact
from tests.conftest import BOB, CONTACTS, FakeWallet, auth
from tests.fakes import tool_call

Factory = Callable[[list[AIMessage]], TestClient]


def transfer_call(contact: str, amount: object) -> AIMessage:
    return tool_call("request_transfer", {"contact": contact, "amount": amount})


def tool_messages(client: TestClient, thread_id: str) -> list[ToolMessage]:
    graph = client.app.state.graph  # type: ignore[attr-defined]
    state = graph.get_state({"configurable": {"thread_id": thread_id}})
    return [m for m in state.values["messages"] if isinstance(m, ToolMessage)]


def start_transfer(client: TestClient, message: str = "Mandale 50000 a María") -> dict:
    response = client.post("/chat", json={"message": message}, headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


def test_transfer_waits_for_confirmation_and_moves_nothing(
    make_client: Factory, wallet: FakeWallet
) -> None:
    client = make_client([transfer_call("María", 50000), AIMessage("Listo")])
    body = start_transfer(client)
    assert body["status"] == "pending_confirmation"
    assert body["summary"] == "Vas a transferir $50.000,00 a María González. ¿Confirmás?"
    assert body["reply"] is None
    assert wallet.transfers == []


def test_confirming_executes_exactly_once_with_the_prepared_request(
    make_client: Factory, wallet: FakeWallet
) -> None:
    client = make_client([transfer_call("María", 50000), AIMessage("Transferencia hecha")])
    pending = start_transfer(client)
    thread_id = pending["thread_id"]
    graph = client.app.state.graph  # type: ignore[attr-defined]
    prepared = graph.get_state({"configurable": {"thread_id": thread_id}}).values[
        "pending_transfer"
    ]

    response = client.post(f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth())
    assert response.status_code == 200
    assert response.json() == {
        "thread_id": thread_id,
        "status": "completed",
        "reply": "Transferencia hecha",
        "summary": None,
    }
    (sent,) = wallet.transfers
    assert sent == {
        "contact_id": "c-maria",
        "amount": "50000.00",
        "idempotency_key": prepared["idempotency_key"],
    }
    assert json.loads(tool_messages(client, thread_id)[-1].content)["status"] == "completed"


def test_declining_never_calls_the_transfer_endpoint(
    make_client: Factory, wallet: FakeWallet
) -> None:
    client = make_client([transfer_call("María", 50000), AIMessage("Cancelé la operación")])
    thread_id = start_transfer(client)["thread_id"]
    response = client.post(f"/chat/{thread_id}/confirm", json={"approve": False}, headers=auth())
    assert response.json()["status"] == "completed"
    assert wallet.transfers == []
    assert json.loads(tool_messages(client, thread_id)[-1].content)["status"] == "cancelled"


def test_wallet_rejection_is_reported_to_the_agent(
    make_client: Factory, wallet: FakeWallet
) -> None:
    wallet.transfer_error = "daily_limit_exceeded"
    client = make_client([transfer_call("María", 900000), AIMessage("Superaste el límite")])
    thread_id = start_transfer(client)["thread_id"]
    response = client.post(f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth())
    assert response.json()["reply"] == "Superaste el límite"
    outcome = json.loads(tool_messages(client, thread_id)[-1].content)
    assert (outcome["status"], outcome["code"]) == ("rejected", "daily_limit_exceeded")


def test_unknown_contact_is_not_guessed(make_client: Factory, wallet: FakeWallet) -> None:
    client = make_client([transfer_call("Pedro", 100), AIMessage("¿Qué Pedro?")])
    body = start_transfer(client)
    assert body["status"] == "completed"
    assert wallet.transfers == []
    assert "No contact matches 'Pedro'" in tool_messages(client, body["thread_id"])[-1].content


def test_ambiguous_contact_asks_instead_of_choosing(
    make_client: Factory, wallet: FakeWallet
) -> None:
    client = make_client([transfer_call("Juan", 100), AIMessage("¿Pérez o Gómez?")])
    body = start_transfer(client)
    assert body["status"] == "completed"
    assert wallet.transfers == []
    content = tool_messages(client, body["thread_id"])[-1].content
    assert "Juan Pérez" in content and "Juan Gómez" in content


def test_invalid_amounts_never_reach_confirmation(make_client: Factory, wallet: FakeWallet) -> None:
    for amount in (-5, 0, "10.123", "abc"):
        client = make_client([transfer_call("María", amount), AIMessage("Monto inválido")])
        assert start_transfer(client)["status"] == "completed"
    assert wallet.transfers == []


def test_transfer_sharing_a_turn_with_other_calls_runs_alone(
    make_client: Factory, wallet: FakeWallet
) -> None:
    mixed = AIMessage(
        content="",
        tool_calls=[
            {"name": "get_balance", "args": {}, "id": "a"},
            {"name": "request_transfer", "args": {"contact": "María", "amount": 10}, "id": "b"},
        ],
    )
    client = make_client([mixed, AIMessage("ok")])
    pending = start_transfer(client)
    assert pending["status"] == "pending_confirmation"
    assert not [r for r in wallet.requests if r.url.path == "/accounts/me/balance"]
    client.post(f"/chat/{pending['thread_id']}/confirm", json={"approve": True}, headers=auth())
    answered = {m.tool_call_id for m in tool_messages(client, pending["thread_id"])}
    assert answered == {"a", "b"}


def test_new_message_is_refused_while_a_confirmation_is_pending(make_client: Factory) -> None:
    client = make_client([transfer_call("María", 50000), AIMessage("ok")])
    thread_id = start_transfer(client)["thread_id"]
    response = client.post(
        "/chat", json={"message": "mejor mandá todo", "thread_id": thread_id}, headers=auth()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "confirmation_pending"


def test_confirm_without_pending_transfer_conflicts(make_client: Factory) -> None:
    client = make_client([AIMessage("Hola")])
    thread_id = client.post("/chat", json={"message": "hola"}, headers=auth()).json()["thread_id"]
    response = client.post(f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_confirm"


def test_decision_must_be_a_real_boolean(make_client: Factory, wallet: FakeWallet) -> None:
    client = make_client([transfer_call("María", 50000), AIMessage("ok")])
    thread_id = start_transfer(client)["thread_id"]
    for value in ("true", 1, "yes", None):
        response = client.post(
            f"/chat/{thread_id}/confirm", json={"approve": value}, headers=auth()
        )
        assert response.status_code == 422
    assert wallet.transfers == []


def test_other_user_cannot_confirm_or_use_the_thread(
    make_client: Factory, wallet: FakeWallet
) -> None:
    client = make_client([transfer_call("María", 50000), AIMessage("ok")])
    thread_id = start_transfer(client)["thread_id"]

    confirm = client.post(f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth(BOB))
    chat = client.post("/chat", json={"message": "hola", "thread_id": thread_id}, headers=auth(BOB))
    assert confirm.status_code == chat.status_code == 404
    assert confirm.json()["error"]["code"] == "thread_not_found"
    assert wallet.transfers == []

    owner = client.post(f"/chat/{thread_id}/confirm", json={"approve": True}, headers=auth())
    assert owner.status_code == 200
    assert len(wallet.transfers) == 1


def test_unknown_thread_looks_the_same_as_a_foreign_one(make_client: Factory) -> None:
    client = make_client([AIMessage("hola")])
    response = client.post(
        "/chat", json={"message": "hola", "thread_id": "does-not-exist"}, headers=auth()
    )
    assert response.status_code == 404


def test_resolve_contact_rules() -> None:
    names = lambda ref: sorted(c["name"] for c in resolve_contact(ref, CONTACTS))  # noqa: E731
    assert names("juan pérez") == ["Juan Pérez"]
    assert names("Juan Perez") == ["Juan Pérez"]
    assert names("maria") == ["María González"]
    assert names("juan") == ["Juan Gómez", "Juan Pérez"]
    assert names("juan.gomez") == ["Juan Gómez"]
    assert names("   ") == []


def test_format_ars() -> None:
    assert format_ars(Decimal("50000")) == "$50.000,00"
    assert format_ars(Decimal("1234567.5")) == "$1.234.567,50"
