import json
import uuid
from decimal import Decimal
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import ValidationError

from agent_service.knowledge import KnowledgeSearch
from agent_service.prompts import SYSTEM_PROMPT
from agent_service.tools import (
    build_knowledge_tool,
    build_read_tools,
    credentials_from,
    request_transfer,
)
from agent_service.transfers import TransferArgs, format_ars, resolve_contact
from agent_service.wallet_client import WalletApiError, WalletClient

TRANSFER_TOOL = "request_transfer"


class PendingTransfer(TypedDict):
    contact_id: str
    contact_name: str
    amount: str
    summary: str
    idempotency_key: str
    tool_call_id: str


class AgentState(MessagesState):
    pending_transfer: NotRequired[PendingTransfer | None]
    approved: NotRequired[bool | None]


def _tool_message(call: dict[str, Any], content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call["id"], name=call["name"])


def _pending(state: AgentState) -> PendingTransfer:
    pending = state.get("pending_transfer")
    if pending is None:
        raise RuntimeError("No pending transfer in state")
    return pending


def route_after_agent(state: AgentState) -> Literal["read_tools", "prepare_transfer", "__end__"]:
    last = state["messages"][-1]
    calls = getattr(last, "tool_calls", None)
    if not calls:
        return END
    if any(c["name"] == TRANSFER_TOOL for c in calls):
        return "prepare_transfer"
    return "read_tools"


def route_after_prepare(state: AgentState) -> Literal["confirm", "agent"]:
    return "confirm" if state.get("pending_transfer") else "agent"


def route_after_confirm(state: AgentState) -> Literal["execute_transfer", "cancel_transfer"]:
    return "execute_transfer" if state.get("approved") is True else "cancel_transfer"


def build_graph(
    model: BaseChatModel,
    wallet: WalletClient,
    checkpointer: BaseCheckpointSaver,
    knowledge: KnowledgeSearch | None = None,
) -> CompiledStateGraph:
    read_tools = build_read_tools(wallet)
    if knowledge is not None:
        read_tools.append(build_knowledge_tool(knowledge))
    llm = model.bind_tools([*read_tools, request_transfer])

    async def agent(state: AgentState) -> dict:
        response = await llm.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    async def prepare_transfer(state: AgentState, config: RunnableConfig) -> dict:
        """Validates the request and resolves the contact. Moves no money."""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            raise RuntimeError("prepare_transfer must follow a model message")
        call = next(c for c in last.tool_calls if c["name"] == TRANSFER_TOOL)
        # Every tool call needs exactly one answer; anything sharing the turn is not run.
        replies = [
            _tool_message(
                c, "Not executed: a transfer must be handled on its own. Retry if needed."
            )
            for c in last.tool_calls
            if c["id"] != call["id"]
        ]

        def rejected(reason: str) -> dict:
            return {
                "messages": [*replies, _tool_message(call, reason)],
                "pending_transfer": None,
            }

        try:
            args = TransferArgs.model_validate(call["args"])
        except ValidationError:
            return rejected("Invalid arguments: a contact name and a numeric amount are required.")
        if not args.amount.is_finite() or args.amount <= 0 or args.amount.as_tuple().exponent < -2:
            return rejected("The amount must be a positive number with at most two decimals.")

        token, request_id = credentials_from(config)
        contacts = await wallet.get("/accounts/me/contacts", token=token, request_id=request_id)
        matches = resolve_contact(args.contact, contacts)
        if not matches:
            names = ", ".join(c["name"] for c in contacts)
            return rejected(
                f"No contact matches '{args.contact}'. The user's contacts are: {names}. "
                "Ask the user who they mean."
            )
        if len(matches) > 1:
            names = ", ".join(c["name"] for c in matches)
            return rejected(
                f"'{args.contact}' matches several contacts: {names}. Ask the user which one."
            )

        contact = matches[0]
        amount = args.amount.quantize(Decimal("0.01"))
        pending: PendingTransfer = {
            "contact_id": contact["id"],
            "contact_name": contact["name"],
            "amount": str(amount),
            "summary": f"Vas a transferir {format_ars(amount)} a {contact['name']}. ¿Confirmás?",
            # Generated here, not in `confirm`: a node containing interrupt() re-runs from the
            # top on resume, so a key created there would change between pause and resume.
            "idempotency_key": uuid.uuid4().hex,
            "tool_call_id": call["id"],
        }
        return {"messages": replies, "pending_transfer": pending, "approved": None}

    def confirm(state: AgentState) -> dict:
        """The approval gate. Nothing above `interrupt` may have side effects."""
        pending = _pending(state)
        decision = interrupt(
            {
                "kind": "transfer_confirmation",
                "summary": pending["summary"],
                "contact_name": pending["contact_name"],
                "amount": pending["amount"],
            }
        )
        return {"approved": decision is True}

    async def execute_transfer(state: AgentState, config: RunnableConfig) -> dict:
        pending = _pending(state)
        token, request_id = credentials_from(config)
        try:
            transfer = await wallet.post(
                "/transfers",
                token=token,
                request_id=request_id,
                json={
                    "contact_id": pending["contact_id"],
                    "amount": pending["amount"],
                    "idempotency_key": pending["idempotency_key"],
                },
            )
            outcome = {
                "status": "completed",
                "amount": transfer["amount"],
                "contact_name": pending["contact_name"],
            }
        except WalletApiError as exc:
            outcome = {"status": "rejected", "code": exc.code, "reason": exc.message}
        return {
            "messages": [
                ToolMessage(
                    json.dumps(outcome, ensure_ascii=False),
                    tool_call_id=pending["tool_call_id"],
                    name=TRANSFER_TOOL,
                )
            ],
            "pending_transfer": None,
            "approved": None,
        }

    async def cancel_transfer(state: AgentState) -> dict:
        pending = _pending(state)
        return {
            "messages": [
                ToolMessage(
                    json.dumps({"status": "cancelled", "reason": "The user declined"}),
                    tool_call_id=pending["tool_call_id"],
                    name=TRANSFER_TOOL,
                )
            ],
            "pending_transfer": None,
            "approved": None,
        }

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    # Tool errors propagate: a failure to reach wallet-api must not be narrated by the model.
    graph.add_node("read_tools", ToolNode(read_tools, handle_tool_errors=False))
    graph.add_node("prepare_transfer", prepare_transfer)
    graph.add_node("confirm", confirm)
    graph.add_node("execute_transfer", execute_transfer)
    graph.add_node("cancel_transfer", cancel_transfer)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent)
    graph.add_edge("read_tools", "agent")
    graph.add_conditional_edges("prepare_transfer", route_after_prepare)
    graph.add_conditional_edges("confirm", route_after_confirm)
    graph.add_edge("execute_transfer", "agent")
    graph.add_edge("cancel_transfer", "agent")
    return graph.compile(checkpointer=checkpointer)
