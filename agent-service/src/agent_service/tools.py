import json
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from agent_service.wallet_client import WalletClient


def credentials_from(config: RunnableConfig) -> tuple[str, str]:
    """The user's token and request id, taken from the run config.

    They are deliberately not graph state: state is persisted and rendered into prompts.
    The token is a SecretStr because LangGraph copies every plain string in `configurable`
    into the persisted checkpoint metadata.
    """
    configurable = config["configurable"]
    return configurable["access_token"].get_secret_value(), configurable.get("request_id", "-")


def build_read_tools(wallet: WalletClient) -> list[BaseTool]:
    async def _fetch(config: RunnableConfig, path: str, **params: Any) -> str:
        token, request_id = credentials_from(config)
        data = await wallet.get(path, token=token, request_id=request_id, params=params or None)
        return json.dumps(data, ensure_ascii=False)

    @tool
    async def get_balance(config: RunnableConfig) -> str:
        """Get the user's current balance in ARS, their daily transfer limit and how much of
        that limit they have already used today."""
        return await _fetch(config, "/accounts/me/balance")

    return [get_balance]
