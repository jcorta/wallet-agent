"""Behavioral evals against a running stack and the configured model.

Run from `agent-service/` with the stack up:

    uv run --env-file ../.env pytest evals -v

Each case drives the public HTTP API as a real user would and asserts, wherever possible, on
wallet-api state (balances, transactions) rather than on the model's wording.
"""

import os
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import pytest

IDP_URL = os.environ.get("IDP_URL", "http://localhost:8001")
WALLET_URL = os.environ.get("WALLET_URL", "http://localhost:8002")
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8003")
# Pause after every model-backed call to stay under free-tier rate limits.
CALL_DELAY_SECONDS = float(os.environ.get("EVAL_DELAY_SECONDS", "3"))
MAX_ATTEMPTS = 5


class Agent:
    """A logged-in user talking to the agent, with retries for rate-limited or flaky calls."""

    def __init__(self, token: str) -> None:
        self.headers = {"Authorization": f"Bearer {token}"}
        self._http = httpx.Client(timeout=180)

    def _post(self, path: str, body: dict[str, Any], expect: int = 200) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._http.post(f"{AGENT_URL}{path}", json=body, headers=self.headers)
            if response.status_code < 500 or attempt == MAX_ATTEMPTS:
                break
            time.sleep(10 * attempt)
        time.sleep(CALL_DELAY_SECONDS)
        assert response.status_code == expect, response.text
        return response

    def chat(self, message: str, thread_id: str | None = None) -> dict[str, Any]:
        return self._post("/chat", {"message": message, "thread_id": thread_id}).json()

    def confirm(self, thread_id: str, approve: bool, expect: int = 200) -> httpx.Response:
        return self._post(f"/chat/{thread_id}/confirm", {"approve": approve}, expect)

    def wallet(self, path: str) -> Any:
        response = self._http.get(f"{WALLET_URL}{path}", headers=self.headers)
        response.raise_for_status()
        return response.json()

    def balance(self) -> Decimal:
        return Decimal(self.wallet("/accounts/me/balance")["balance"])

    def transactions(self) -> list[dict[str, Any]]:
        return self.wallet("/accounts/me/transactions?limit=50")


def _login(username: str, password: str) -> Agent:
    response = httpx.post(
        f"{IDP_URL}/login", json={"username": username, "password": password}, timeout=10
    )
    response.raise_for_status()
    return Agent(response.json()["access_token"])


@pytest.fixture(scope="session")
def alice() -> Agent:
    return _login(os.environ["USER1_USERNAME"], os.environ["USER1_PASSWORD"])


@pytest.fixture(scope="session")
def bob() -> Agent:
    return _login(os.environ["USER2_USERNAME"], os.environ["USER2_PASSWORD"])


def amounts_in(text: str) -> set[Decimal]:
    """Every number in `text`, read as either 1.234,56 (es-AR) or 1,234.56 notation."""
    found: set[Decimal] = set()
    for token in re.findall(r"\d[\d.,]*\d|\d", text):
        last_dot, last_comma = token.rfind("."), token.rfind(",")
        decimal_sep = "." if last_dot > last_comma else ","
        has_decimals = (
            max(last_dot, last_comma) >= 0 and len(token) - max(last_dot, last_comma) - 1 != 3
        )
        if has_decimals or (last_dot >= 0 and last_comma >= 0):
            integer, _, fraction = token.rpartition(decimal_sep)
            normalized = re.sub(r"[.,]", "", integer) + "." + fraction
        else:
            normalized = re.sub(r"[.,]", "", token)
        try:
            found.add(Decimal(normalized))
        except InvalidOperation:
            continue
    return found
