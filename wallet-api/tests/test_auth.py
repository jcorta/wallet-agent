import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, auth, make_token

PROTECTED = ["/accounts/me/balance", "/accounts/me/transactions", "/accounts/me/contacts"]


@pytest.mark.parametrize("path", PROTECTED)
def test_missing_token_is_rejected(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"


def test_garbage_token_is_rejected(client: TestClient) -> None:
    response = client.get("/accounts/me/balance", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_expired_token_is_rejected(client: TestClient) -> None:
    response = client.get("/accounts/me/balance", headers=auth(ALICE, ttl=-10))
    assert response.status_code == 401


def test_token_signed_with_another_key_is_rejected(client: TestClient) -> None:
    forged = make_token(ALICE, key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    response = client.get("/accounts/me/balance", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_wrong_audience_is_rejected(client: TestClient) -> None:
    response = client.get("/accounts/me/balance", headers=auth(ALICE, audience="other"))
    assert response.status_code == 401


def test_wrong_issuer_is_rejected(client: TestClient) -> None:
    response = client.get("/accounts/me/balance", headers=auth(ALICE, issuer="other"))
    assert response.status_code == 401


def test_token_of_another_user_only_sees_that_users_data(client: TestClient) -> None:
    alice = client.get("/accounts/me/contacts", headers=auth(ALICE)).json()
    bob = client.get("/accounts/me/contacts", headers=auth(BOB)).json()
    assert {c["alias"] for c in alice} == {"juan", "juan.gomez", "maria"}
    assert {c["alias"] for c in bob} == {"ana", "carlos"}
    assert client.get("/accounts/me/balance", headers=auth(BOB)).json()["balance"] == "100000.00"


def test_user_identity_in_query_is_ignored(client: TestClient) -> None:
    response = client.get(f"/accounts/me/balance?user_id={ALICE}", headers=auth(BOB))
    assert response.json()["balance"] == "100000.00"


def test_error_body_is_uniform_and_carries_request_id(client: TestClient) -> None:
    response = client.get("/accounts/me/balance", headers={"X-Request-ID": "req-42"})
    assert response.json()["error"]["request_id"] == "req-42"
    assert response.headers["x-request-id"] == "req-42"


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
