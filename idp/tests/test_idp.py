import time

import httpx
import jwt
from fastapi.testclient import TestClient

from idp.keys import KeyStore
from idp.settings import Settings
from tests.conftest import USER1_ID


def _login(
    client: TestClient, username: str = "alice", password: str = "alice-pw"
) -> httpx.Response:
    return client.post("/login", json={"username": username, "password": password})


def _verify(client: TestClient, settings: Settings, token: str) -> dict:
    jwks = client.get("/.well-known/jwks.json").json()
    key = jwt.PyJWK(jwks["keys"][0]).key
    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )


def test_login_returns_token_verifiable_with_jwks(client: TestClient, settings: Settings) -> None:
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    claims = _verify(client, settings, body["access_token"])
    assert claims["sub"] == USER1_ID


def test_token_expires_in_fifteen_minutes(client: TestClient, settings: Settings) -> None:
    token = _login(client).json()["access_token"]
    claims = _verify(client, settings, token)
    assert 890 <= claims["exp"] - int(time.time()) <= 900


def test_token_header_references_published_key(client: TestClient) -> None:
    token = _login(client).json()["access_token"]
    kid = jwt.get_unverified_header(token)["kid"]
    assert kid == client.get("/.well-known/jwks.json").json()["keys"][0]["kid"]


def test_wrong_password_is_rejected_with_uniform_error(client: TestClient) -> None:
    response = _login(client, password="nope")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials"
    assert error["request_id"] == response.headers["x-request-id"]


def test_unknown_user_is_rejected(client: TestClient) -> None:
    assert _login(client, username="mallory").status_code == 401


def test_malformed_body_uses_uniform_error(client: TestClient) -> None:
    response = client.post("/login", json={"username": "alice"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


def test_key_is_stable_across_restarts(settings: Settings) -> None:
    assert KeyStore(settings.key_path).kid == KeyStore(settings.key_path).kid
