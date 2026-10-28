import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth, contact_id
from wallet_api.schemas import TransferRequest
from wallet_api.service import execute_transfer


def _transfer(
    client: TestClient,
    caller: str,
    contact: str,
    amount: str,
    key: str | None = None,
    **extra: Any,
):
    body = {
        "contact_id": contact,
        "amount": amount,
        "idempotency_key": key or uuid.uuid4().hex,
        **extra,
    }
    return client.post("/transfers", json=body, headers=auth(caller))


def _balance(client: TestClient, user_id: str) -> Decimal:
    return Decimal(client.get("/accounts/me/balance", headers=auth(user_id)).json()["balance"])


def test_successful_transfer_moves_money_and_is_listed(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    response = _transfer(client, ALICE, juan, "50000.00")
    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert _balance(client, ALICE) == Decimal("450000.00")
    assert _balance(client, BOB) == Decimal("150000.00")

    latest = client.get("/accounts/me/transactions", headers=auth(ALICE)).json()[0]
    assert (latest["direction"], latest["amount"], latest["counterparty_name"]) == (
        "outgoing",
        "50000.00",
        "Juan Pérez",
    )
    incoming = client.get("/accounts/me/transactions", headers=auth(BOB)).json()[0]
    assert (incoming["direction"], incoming["counterparty_name"]) == ("incoming", "Ana Fernández")


def test_insufficient_funds_is_rejected_and_audited(
    client: TestClient, run_sql: Callable[[str], Any]
) -> None:
    run_sql("UPDATE accounts SET daily_limit = 10000000")
    response = _transfer(client, ALICE, contact_id(client, ALICE, "juan"), "600000.00")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_funds"
    assert _balance(client, ALICE) == Decimal("500000.00")
    rows = run_sql("SELECT status, rejection_reason FROM transfers WHERE amount = 600000")
    assert rows == [("rejected", "insufficient_funds")]


def test_accumulated_daily_limit_is_enforced(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    assert _transfer(client, ALICE, juan, "150000.00").status_code == 201
    response = _transfer(client, ALICE, juan, "60000.00")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "daily_limit_exceeded"
    assert _transfer(client, ALICE, juan, "50000.00").status_code == 201


def test_rejected_transfers_do_not_count_towards_the_limit(
    client: TestClient, run_sql: Callable[[str], Any]
) -> None:
    run_sql("UPDATE accounts SET balance = 1000 WHERE balance = 500000")
    juan = contact_id(client, ALICE, "juan")
    assert _transfer(client, ALICE, juan, "5000.00").status_code == 422
    spent = client.get("/accounts/me/balance", headers=auth(ALICE)).json()["spent_today"]
    assert Decimal(spent) == 0


def test_contact_of_another_user_is_rejected(client: TestClient) -> None:
    bobs_contact = contact_id(client, BOB, "carlos")
    response = _transfer(client, ALICE, bobs_contact, "100.00")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_contact"
    assert _balance(client, ALICE) == Decimal("500000.00")


def test_random_contact_is_rejected(client: TestClient) -> None:
    response = _transfer(client, ALICE, str(uuid.uuid4()), "100.00")
    assert response.json()["error"]["code"] == "unknown_contact"


def test_non_positive_amounts_are_rejected(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    for amount in ("0.00", "-500.00"):
        response = _transfer(client, ALICE, juan, amount)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_amount"
    assert _balance(client, BOB) == Decimal("100000.00")


def test_amount_with_more_than_two_decimals_is_a_validation_error(client: TestClient) -> None:
    response = _transfer(client, ALICE, contact_id(client, ALICE, "juan"), "10.001")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_user_id_in_body_is_ignored(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    response = _transfer(client, BOB, juan, "100.00", user_id=ALICE)
    assert response.json()["error"]["code"] == "unknown_contact"
    assert _balance(client, ALICE) == Decimal("500000.00")


def test_retry_with_same_key_debits_once(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    first = _transfer(client, ALICE, juan, "1000.00", key="retry-key-1")
    second = _transfer(client, ALICE, juan, "1000.00", key="retry-key-1")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert _balance(client, ALICE) == Decimal("499000.00")
    assert _balance(client, BOB) == Decimal("101000.00")


def test_retry_of_rejected_transfer_returns_same_rejection(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    first = _transfer(client, ALICE, juan, "900000.00", key="retry-key-2")
    second = _transfer(client, ALICE, juan, "900000.00", key="retry-key-2")
    assert first.status_code == second.status_code == 422
    assert first.json()["error"]["code"] == second.json()["error"]["code"]


def test_same_key_with_different_payload_conflicts(client: TestClient) -> None:
    juan = contact_id(client, ALICE, "juan")
    assert _transfer(client, ALICE, juan, "1000.00", key="retry-key-3").status_code == 201
    response = _transfer(client, ALICE, juan, "2000.00", key="retry-key-3")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"
    assert _balance(client, ALICE) == Decimal("499000.00")


def test_idempotency_keys_are_scoped_per_user(client: TestClient) -> None:
    ana = contact_id(client, BOB, "ana")
    juan = contact_id(client, ALICE, "juan")
    assert _transfer(client, BOB, ana, "100.00", key="shared-key-1").status_code == 201
    assert _transfer(client, ALICE, juan, "100.00", key="shared-key-1").status_code == 201


def test_concurrent_transfers_never_overdraw(
    client: TestClient, run_sql: Callable[[str], Any]
) -> None:
    run_sql("UPDATE accounts SET balance = 250000, daily_limit = 10000000 WHERE balance = 500000")
    juan = uuid.UUID(contact_id(client, ALICE, "juan"))
    sessions = client.app.state.sessionmaker  # type: ignore[attr-defined]

    def attempt(_: int) -> str:
        request = TransferRequest(
            contact_id=juan, amount=Decimal("100000.00"), idempotency_key=uuid.uuid4().hex
        )
        with sessions() as session:
            transfer, _ = execute_transfer(
                session, ALICE, request, "America/Argentina/Buenos_Aires"
            )
            return transfer.status

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(10)))

    assert outcomes.count("completed") == 2
    assert _balance(client, ALICE) == Decimal("50000.00")
    assert _balance(client, BOB) == Decimal("300000.00")


def test_opposite_concurrent_transfers_do_not_deadlock(client: TestClient) -> None:
    to_juan = uuid.UUID(contact_id(client, ALICE, "juan"))
    to_ana = uuid.UUID(contact_id(client, BOB, "ana"))
    sessions = client.app.state.sessionmaker  # type: ignore[attr-defined]

    def attempt(i: int) -> str:
        user, contact = (ALICE, to_juan) if i % 2 == 0 else (BOB, to_ana)
        request = TransferRequest(
            contact_id=contact, amount=Decimal("10.00"), idempotency_key=uuid.uuid4().hex
        )
        with sessions() as session:
            return execute_transfer(session, user, request, "UTC")[0].status

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(40)))

    assert set(outcomes) == {"completed"}
    assert _balance(client, ALICE) + _balance(client, BOB) == Decimal("600000.00")


def test_transactions_only_list_own_activity_newest_first(client: TestClient) -> None:
    items = client.get("/accounts/me/transactions", headers=auth(ALICE)).json()
    assert [i["counterparty_name"] for i in items] == ["María González", "Juan Pérez"]
    assert client.get("/accounts/me/transactions?limit=1", headers=auth(ALICE)).json()[1:] == []
    assert client.get("/accounts/me/transactions", headers=auth(BOB)).json()[0]["direction"] == (
        "incoming"
    )
