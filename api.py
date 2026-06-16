import os
import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from graph.build import build_graph

app   = FastAPI()
graph = build_graph()


class Question(BaseModel):
    question: str


def _build_steps(result: dict) -> list[dict]:
    """Reconstruct execution steps from the final graph state."""
    steps   = []
    required = result.get("required_agents", [])

    # orchestrator
    agent_str = ", ".join(required) if required else "none"
    steps.append({
        "node":   "orchestrator",
        "label":  "Intent Classification",
        "detail": f"Routing to: {agent_str}",
    })

    # sql_agent
    if "sql" in required:
        sql_r = result.get("sql_result") or {}
        if sql_r.get("error"):
            detail = f"Blocked — {sql_r['error']}"
        else:
            tables = ", ".join(sql_r.get("tables", [])) or "—"
            n_rows = len(sql_r.get("rows", []))
            detail = f"Queried: {tables} — {n_rows} row(s)"
        steps.append({"node": "sql_agent", "label": "Database Query", "detail": detail})

    # rag_agent
    if "rag" in required:
        rag_r   = result.get("rag_result") or {}
        sources = list(set(rag_r.get("sources", [])))
        detail  = f"Retrieved from: {', '.join(sources)}" if sources else "No chunks found"
        steps.append({"node": "rag_agent", "label": "Contract Retrieval", "detail": detail})

    # synthesis
    steps.append({
        "node":   "synthesis",
        "label":  "Answer Assembly",
        "detail": "Grounded answer with source citations",
    })

    return steps


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/ask")
async def ask(body: Question):
    result = graph.invoke({
        "question":        body.question,
        "required_agents": [],
        "sql_result":      None,
        "rag_result":      None,
        "final_answer":    "",
    })

    sql_r     = result.get("sql_result") or {}
    rag_r     = result.get("rag_result") or {}
    tables    = sql_r.get("tables", [])
    documents = list(set(rag_r.get("sources", [])))

    return {
        "steps":     _build_steps(result),
        "answer":    result.get("final_answer", ""),
        "tables":    tables,
        "documents": documents,
    }


app.mount("/", StaticFiles(directory="static", html=True), name="static")
