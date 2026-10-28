import json
from decimal import Decimal
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from agent_service.knowledge import KnowledgeSearch
from agent_service.transfers import TransferArgs
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

    @tool
    async def get_transactions(config: RunnableConfig, limit: int = 10) -> str:
        """List the user's most recent transfers, newest first, both sent and received,
        including rejected attempts. `limit` is the number of items (1 to 50)."""
        return await _fetch(config, "/accounts/me/transactions", limit=max(1, min(limit, 50)))

    @tool
    async def list_contacts(config: RunnableConfig) -> str:
        """List the contacts the user can transfer money to."""
        return await _fetch(config, "/accounts/me/contacts")

    return [get_balance, get_transactions, list_contacts]


def build_knowledge_tool(knowledge: KnowledgeSearch) -> BaseTool:
    @tool
    async def search_knowledge(query: str) -> str:
        """Search the wallet's help documents about fees, settlement times and account limits.
        Use it for general questions about how the wallet works, not for the user's own data."""
        passages = await knowledge.search(query)
        return json.dumps(
            [{"source": p.source, "content": p.content} for p in passages], ensure_ascii=False
        )

    return search_knowledge


@tool(args_schema=TransferArgs)
def request_transfer(contact: str, amount: Decimal) -> str:
    """Start a money transfer to one of the user's contacts. This does not move any money:
    the user is asked to confirm first, and the tool result tells you whether it was
    completed, rejected or cancelled."""
    raise RuntimeError("request_transfer is handled by the approval flow, never executed directly")
