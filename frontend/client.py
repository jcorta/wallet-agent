from typing import Any

import httpx

REQUEST_TIMEOUT_SECONDS = 120.0


class SessionExpired(Exception):
    """The access token was rejected; the user has to log in again."""


class BackendError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Backend:
    def __init__(
        self, idp_url: str, agent_url: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._idp_url = idp_url.rstrip("/")
        self._agent_url = agent_url.rstrip("/")
        self._transport = transport

    def _request(
        self, method: str, url: str, token: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport) as http:
                response = http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError("No se pudo conectar con el servicio.") from exc
        if response.is_success:
            return response.json()
        if token and response.status_code == 401:
            raise SessionExpired
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError):
            message = "Ocurrió un error inesperado."
        raise BackendError(message)

    def login(self, username: str, password: str) -> str:
        body = {"username": username, "password": password}
        return self._request("POST", f"{self._idp_url}/login", json=body)["access_token"]

    def chat(self, token: str, message: str, thread_id: str | None) -> dict[str, Any]:
        body = {"message": message, "thread_id": thread_id}
        return self._request("POST", f"{self._agent_url}/chat", token, json=body)

    def confirm(self, token: str, thread_id: str, approve: bool) -> dict[str, Any]:
        url = f"{self._agent_url}/chat/{thread_id}/confirm"
        return self._request("POST", url, token, json={"approve": approve})
