from typing import Any

import httpx

REQUEST_ID_HEADER = "X-Request-ID"


class WalletApiError(Exception):
    """A business-level rejection from wallet-api that the agent can explain to the user."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class UpstreamError(Exception):
    """wallet-api is unreachable, or rejected the user's credentials."""

    def __init__(self, message: str, *, token_rejected: bool = False) -> None:
        super().__init__(message)
        self.token_rejected = token_rejected


class WalletClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self, path: str, *, token: str, request_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._send("GET", path, token, request_id, params=params)

    async def post(self, path: str, *, token: str, request_id: str, json: dict[str, Any]) -> Any:
        return await self._send("POST", path, token, request_id, json=json)

    async def _send(
        self, method: str, path: str, token: str, request_id: str, **kwargs: Any
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}", REQUEST_ID_HEADER: request_id}
        try:
            response = await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamError("wallet-api is unreachable") from exc

        if response.is_success:
            return response.json()
        if response.status_code == 401:
            raise UpstreamError("wallet-api rejected the token", token_rejected=True)
        if response.status_code in (409, 422):
            error = response.json().get("error", {})
            raise WalletApiError(
                response.status_code,
                error.get("code", "rejected"),
                error.get("message", "The operation was rejected"),
            )
        raise UpstreamError(f"wallet-api returned {response.status_code}")
