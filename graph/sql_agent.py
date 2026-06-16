import os
import re

import anthropic
import psycopg2
import sqlparse
from langfuse import observe

from graph.state import AgentState

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SQL_AGENT_DATABASE_URL = os.environ.get(
    "SQL_AGENT_DATABASE_URL",
    "postgresql://sp_readonly:readonly@localhost:5432/salespilot",
)

_SCHEMA = """\
accounts(account_id, name, industry, region, created_at)
contacts(contact_id, account_id, name, email, role)
products(product_id, sku, name, category, list_price)
orders(order_id, account_id, order_date, status)
order_items(item_id, order_id, product_id, quantity, unit_price)
contracts(contract_id, account_id, discount_rate, renewal_date, doc_filename)

Relationships:
- orders.account_id        → accounts.account_id
- order_items.order_id     → orders.order_id
- order_items.product_id   → products.product_id
- contracts.account_id     → accounts.account_id
- contacts.account_id      → accounts.account_id

Notes:
- order_items.unit_price   is the discounted price actually charged (not list_price)
- contracts.discount_rate  is a decimal (0.12 = 12%)
- orders.order_date        is a DATE column
"""

_SYSTEM_PROMPT = f"""\
You are a SQL expert. Given a business question, write a single PostgreSQL SELECT query.

Database schema:
{_SCHEMA}

Rules:
- Write ONLY the SQL query — no explanation, no markdown, no code fences
- Single SELECT statement only, no semicolons
- Never write INSERT, UPDATE, DELETE, DROP, ALTER, GRANT, TRUNCATE, or COPY
- When filtering by company or account name, always use ILIKE '%name%' (not = 'name') to handle partial matches (e.g. user says "Acme" but DB has "Acme Corp")
"""

_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "GRANT", "REVOKE", "TRUNCATE", "COPY", "CREATE",
}


def _extract_sql(text: str) -> str:
    """Strip markdown code fences and leading explanation text."""
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def _validate(sql: str) -> tuple[bool, str]:
    """White-list soft door.

    Primary gate: sqlparse must parse this as exactly one SELECT statement.
    If Claude returned a refusal or explanation instead of SQL, sqlparse finds
    no recognisable statement type → rejected here, not by keyword scan.

    Keyword blacklist is secondary defence-in-depth only — it never runs on
    natural-language text, only on text that already passed as valid SQL.
    """
    # Primary: parse and require exactly one SELECT
    statements = [s for s in sqlparse.parse(sql) if s.get_type() is not None]

    if not statements:
        return False, "Not a valid SQL statement — Claude may have returned an explanation"

    if len(statements) > 1:
        return False, f"Expected 1 statement, got {len(statements)}"

    stmt_type = statements[0].get_type()
    if stmt_type != "SELECT":
        return False, f"Only SELECT allowed, got: {stmt_type}"

    # Secondary: keyword scan as extra safety net (runs only on confirmed SQL)
    upper = sql.upper()
    for kw in _FORBIDDEN:
        if re.search(rf"\b{kw}\b", upper):
            return False, f"Forbidden keyword in SELECT body: {kw}"

    return True, "ok"


def _extract_tables(sql: str) -> list[str]:
    return list(set(
        re.findall(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
    ))


def _run_query(sql: str) -> list[dict]:
    """Execute via sp_readonly (hard wall — SELECT-only grants at DB level)."""
    conn = psycopg2.connect(SQL_AGENT_DATABASE_URL)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


@observe()
def sql_agent_node(state: AgentState) -> dict:
    print(f"[sql_agent]     generating SQL for: '{state['question']}'")

    # 1 — generate SQL
    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": state["question"]}],
    )
    raw = response.content[0].text

    # 2 — strip markdown / explanation text
    sql = _extract_sql(raw)
    print(f"[sql_agent]     SQL: {sql[:120]}{'...' if len(sql) > 120 else ''}")

    # 3 — soft door: application-layer validation
    safe, reason = _validate(sql)
    if not safe:
        print(f"[sql_agent]     BLOCKED by soft door — {reason}")
        return {"sql_result": {"query": sql, "rows": [], "tables": [], "error": reason}}

    # 4 — hard wall: execute through sp_readonly
    try:
        rows = _run_query(sql)
        tables = _extract_tables(sql)
        print(f"[sql_agent]     {len(rows)} rows from tables: {tables}")
        return {"sql_result": {"query": sql, "rows": rows, "tables": tables}}
    except Exception as exc:
        print(f"[sql_agent]     DB error: {exc}")
        return {"sql_result": {"query": sql, "rows": [], "tables": [], "error": str(exc)}}
