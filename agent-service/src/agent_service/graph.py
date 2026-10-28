from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from agent_service.prompts import SYSTEM_PROMPT


class AgentState(MessagesState):
    pass


def route_after_agent(state: AgentState) -> Literal["read_tools", "__end__"]:
    last = state["messages"][-1]
    return "read_tools" if getattr(last, "tool_calls", None) else END


def build_graph(
    model: BaseChatModel,
    read_tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    llm = model.bind_tools(read_tools)

    async def agent(state: AgentState) -> dict:
        response = await llm.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    # Tool errors propagate: a failure to reach wallet-api must not be narrated by the model.
    graph.add_node("read_tools", ToolNode(read_tools, handle_tool_errors=False))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent)
    graph.add_edge("read_tools", "agent")
    return graph.compile(checkpointer=checkpointer)
