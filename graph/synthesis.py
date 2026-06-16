import os
import anthropic
from langfuse import observe
from graph.state import AgentState

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

_SYSTEM_PROMPT = """\
You are a sales intelligence assistant. Your job is to answer business questions
using ONLY the data provided below — never fabricate or estimate.

Rules:
1. Every factual claim MUST be followed by its source in parentheses:
   (source: orders table) or (source: acme_corp_msa.md)
2. You may only cite sources that appear in the data provided to you.
3. If the provided data does not contain enough information to answer,
   respond exactly: "I could not find this in the available data."
4. Be concise. One short paragraph is enough.
"""


def _format_sql(sql_result: dict | None) -> str:
    if not sql_result or sql_result.get("error") or not sql_result.get("rows"):
        return ""
    tables = ", ".join(sql_result.get("tables", []))
    rows   = sql_result["rows"]
    lines  = [f"SQL query results (from tables: {tables}):"]
    for row in rows[:20]:          # cap at 20 rows to stay within context
        lines.append(str(row))
    return "\n".join(lines)


def _format_rag(rag_result: dict | None) -> str:
    if not rag_result or not rag_result.get("chunks"):
        return ""
    lines = ["Contract document excerpts:"]
    for chunk, source in zip(rag_result["chunks"], rag_result["sources"]):
        lines.append(f"[{source}]\n{chunk}\n")
    return "\n".join(lines)


@observe()
def synthesis_node(state: AgentState) -> dict:
    sql_section = _format_sql(state.get("sql_result"))
    rag_section = _format_rag(state.get("rag_result"))

    if not sql_section and not rag_section:
        print("[synthesis]     no data from either agent — returning not-found")
        return {"final_answer": "I could not find this in the available data."}

    data_block = "\n\n".join(filter(None, [sql_section, rag_section]))

    user_message = f"Question: {state['question']}\n\nData:\n{data_block}"

    print(f"[synthesis]     assembling answer (sql={'yes' if sql_section else 'no'}, "
          f"rag={'yes' if rag_section else 'no'})")

    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    answer = response.content[0].text.strip()
    print(f"[synthesis]     answer: {answer[:120]}{'...' if len(answer) > 120 else ''}")
    return {"final_answer": answer}
