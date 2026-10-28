import json

import httpx
import pytest

from client import Backend, BackendError, SessionExpired


def backend_with(handler) -> Backend:
    return Backend("http://idp", "http://agent", httpx.MockTransport(handler))


def test_login_returns_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login"
        assert json.loads(request.content) == {"username": "alice", "password": "pw"}
        return httpx.Response(200, json={"access_token": "tok"})

    assert backend_with(handler).login("alice", "pw") == "tok"


def test_failed_login_surfaces_the_server_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error = {"code": "invalid_credentials", "message": "Invalid username or password"}
        return httpx.Response(401, json={"error": error})

    with pytest.raises(BackendError, match="Invalid username"):
        backend_with(handler).login("alice", "bad")


def test_chat_and_confirm_send_the_token_and_payloads() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"thread_id": "t1", "status": "completed", "reply": "ok"})

    backend = backend_with(handler)
    backend.chat("tok", "hola", None)
    backend.confirm("tok", "t1", True)

    assert [r.url.path for r in seen] == ["/chat", "/chat/t1/confirm"]
    assert all(r.headers["authorization"] == "Bearer tok" for r in seen)
    assert json.loads(seen[1].content) == {"approve": True}


def test_rejected_token_means_session_expired() -> None:
    backend = backend_with(lambda r: httpx.Response(401, json={"error": {"message": "x"}}))
    with pytest.raises(SessionExpired):
        backend.chat("tok", "hola", None)


def test_unreachable_backend_is_a_user_facing_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(BackendError):
        backend_with(handler).chat("tok", "hola", None)
