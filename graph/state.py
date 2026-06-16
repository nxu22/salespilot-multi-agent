from typing import TypedDict


class AgentState(TypedDict):
    question:        str
    required_agents: list[str]   # ["sql"], ["rag"], or ["sql", "rag"]
    sql_result:      dict | None  # {"query": str, "rows": list, "tables": list[str]}
    rag_result:      dict | None  # {"chunks": list, "sources": list[str]}
    final_answer:    str
