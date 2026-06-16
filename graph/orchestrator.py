import os
import anthropic
from langfuse import observe
from graph.state import AgentState

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

_ROUTE_TOOL = {
    "name": "route_question",
    "description": (
        "Decide which agents are needed to answer a sales question. "
        "sql_agent queries structured data (orders, accounts, products, revenue, dates). "
        "rag_agent queries contract documents (discount terms, contract prices, renewal dates). "
        "Return both when the question requires comparing structured data against contract terms."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "required_agents": {
                "type": "array",
                "items": {"type": "string", "enum": ["sql", "rag"]},
                "description": "Agents needed to answer the question.",
            }
        },
        "required": ["required_agents"],
    },
}

_SYSTEM_PROMPT = """\
You are a routing assistant for a sales intelligence system. Given a question,
call the route_question tool to specify which agents are needed.

sql_agent  — use for questions about orders, customers, products, revenue, dates, counts.
rag_agent  — use for questions about contract terms, discount rates, pricing clauses, renewal dates.

Examples:
- "Who are our biggest customers by revenue this year?"     → ["sql"]
- "What payment terms does Globex's contract specify?"      → ["rag"]
- "Which products haven't been ordered in the last month?"  → ["sql"]
- "Does Initech get a volume discount per their agreement?" → ["rag"]
- "How does the catalog price for product X compare to what Umbrella Ltd pays per contract?" → ["sql", "rag"]
- "Show me recent orders and what discount rate applies."   → ["sql", "rag"]
"""


@observe()
def orchestrator_node(state: AgentState) -> dict:
    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        tools=[_ROUTE_TOOL],
        tool_choice={"type": "any"},   # forces a tool call, no free-text allowed
        messages=[{"role": "user", "content": state["question"]}],
    )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    required_agents = tool_block.input["required_agents"]

    print(f"[orchestrator]  question='{state['question']}'  route={required_agents}")
    return {"required_agents": required_agents}
