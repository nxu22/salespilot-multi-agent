from langgraph.graph import StateGraph, END
from langgraph.types import Send

from graph.state import AgentState
from graph.orchestrator import orchestrator_node
from graph.sql_agent import sql_agent_node
from graph.rag_agent import rag_agent_node
from graph.synthesis import synthesis_node


def _dispatch(state: AgentState):
    """Fan out to required agents in parallel using Send.
    Returns one Send per agent; LangGraph executes them concurrently and merges
    state before passing the combined result to synthesis.
    If required_agents is empty (LLM returned nothing useful), skip to synthesis
    so the graph never stalls and the user gets an honest 'not found'."""
    if not state["required_agents"]:
        return [Send("synthesis", state)]
    return [Send(f"{agent}_agent", state) for agent in state["required_agents"]]


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("orchestrator", orchestrator_node)
    g.add_node("sql_agent",    sql_agent_node)
    g.add_node("rag_agent",    rag_agent_node)
    g.add_node("synthesis",    synthesis_node)

    g.set_entry_point("orchestrator")
    g.add_conditional_edges("orchestrator", _dispatch)  # fan-out via Send
    g.add_edge("sql_agent", "synthesis")                # fan-in: both branches converge here
    g.add_edge("rag_agent", "synthesis")
    g.add_edge("synthesis",  END)

    return g.compile()
