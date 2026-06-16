#!/usr/bin/env python3
"""
End-to-end eval harness — validates final answers for the 4 acceptance questions
plus one honest-refusal case.

Run from project root:
  python tests/eval_e2e.py

Requires: Docker running + salespilot-pg container up + .env loaded.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


# ── 0. Pre-flight: DB connectivity check ────────────────────────────────────

def _check_db() -> bool:
    import os
    import psycopg2
    try:
        conn = psycopg2.connect(os.environ["SQL_AGENT_DATABASE_URL"])
        conn.close()
        return True
    except Exception:
        return False


# ── 1. Case definitions ──────────────────────────────────────────────────────

# Q1 dormant names come from the DB at runtime (see _get_dormant_names()).
# Listed here as a fallback reference only — not used directly.
_KNOWN_DORMANT = [
    "Cogsworth Co", "Flintstones LLC", "Slate Rock Inc",
    "Spacely Sprockets", "Vandelay Ind",
]

CASES = [
    {
        "id": "Q1",
        "question": "Which accounts haven't ordered in 90 days?",
        # must_contain is filled at runtime from the DB
        "must_contain_dynamic": "dormant_names",
        "must_sources": ["accounts", "orders"],
        "honest_refusal": False,
    },
    {
        "id": "Q2",
        "question": "What's the contract discount for Acme Corp?",
        # 12 matches "12%", "12.0%", "0.12" via number extraction
        "must_contain": ["12"],
        "must_sources": ["acme_corp_msa.md"],
        "honest_refusal": False,
    },
    {
        "id": "Q3",
        "question": "Top 5 products by revenue this quarter?",
        # Don't assert specific Faker-generated product names — they change on re-seed.
        # Assert: answer contains at least one revenue figure (a dollar amount or number).
        "must_contain": [],          # revenue number checked separately in Q3 validator
        "must_sources": ["products", "order_items"],
        "honest_refusal": False,
        "q3_special": True,          # triggers row-count + revenue-number checks
    },
    {
        "id": "Q4",
        "question": "Compare Acme's contract price vs catalog price for product PX-1000",
        "must_contain": ["1250", "1100"],
        # Both SQL source (products table) AND RAG source (contract doc) must appear.
        # If SQL returns 0 rows and RAG alone supplies both numbers, "products" will be
        # absent from sql_result["tables"] and this case will FAIL — catching silent failures.
        "must_sources": ["products", "acme_corp_msa.md"],
        "honest_refusal": False,
    },
    {
        "id": "Q5",
        "question": "What's the weather like today?",
        "must_contain": [],
        "must_sources": [],
        "honest_refusal": True,
        # NOTE: "could not find" is tied to the exact refusal wording in synthesis.py.
        # If you change the refusal phrase there, update _REFUSAL_PHRASES below too.
    },
]

_REFUSAL_PHRASES = ["could not find", "not available", "no data", "unable to find"]


# ── 2. Helpers ───────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Strip currency symbols, commas, and trailing zeros for number matching."""
    return re.sub(r"[$,]", "", text)


def _contains_number(answer: str, target: str) -> bool:
    """Check if a numeric string (e.g. '1250') appears in the answer,
    ignoring formatting like $1,250.00 or 1250.0."""
    norm = _normalise(answer)
    # Match the digits anywhere (e.g. 1250 inside 1250.00)
    return bool(re.search(rf"\b{re.escape(target)}", norm))


def _actual_sources(result: dict) -> list[str]:
    sql_tables = (result.get("sql_result") or {}).get("tables", [])
    rag_sources = (result.get("rag_result") or {}).get("sources", [])
    return sql_tables + rag_sources


def _source_present(source: str, actual: list[str]) -> bool:
    return any(source.lower() in s.lower() for s in actual)


def _get_dormant_names() -> list[str]:
    import os, psycopg2
    conn = psycopg2.connect(os.environ["SQL_AGENT_DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("""
        SELECT a.name FROM accounts a
        LEFT JOIN orders o
          ON o.account_id = a.account_id
         AND o.order_date >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY a.account_id, a.name
        HAVING COUNT(o.order_id) = 0
        ORDER BY a.name
    """)
    names = [r[0] for r in cur.fetchall()]
    conn.close()
    return names


# ── 3. Single-case evaluator ─────────────────────────────────────────────────

def evaluate(case: dict, result: dict) -> tuple[bool, list[str]]:
    """Returns (passed, list_of_failure_reasons)."""
    failures = []
    answer  = result.get("final_answer", "")
    sources = _actual_sources(result)

    if case.get("honest_refusal"):
        if not any(p in answer.lower() for p in _REFUSAL_PHRASES):
            failures.append(f"Expected refusal phrase, got: {answer[:120]}")
        return (len(failures) == 0), failures

    # must_contain checks
    for target in case.get("must_contain", []):
        if not _contains_number(answer, target):
            failures.append(f"'{target}' not found in answer")

    # must_sources checks
    for src in case.get("must_sources", []):
        if not _source_present(src, sources):
            failures.append(f"Source '{src}' missing (actual: {sources})")

    # Q3 special: at least one $ or revenue figure in the answer
    if case.get("q3_special"):
        if not re.search(r"\$[\d,]+|\d[\d,]+\.\d{2}", answer):
            failures.append("No revenue figure found in Q3 answer")

    return (len(failures) == 0), failures


# ── 4. Main runner ───────────────────────────────────────────────────────────

def main() -> None:
    # Pre-flight DB check
    if not _check_db():
        print("ERROR: Cannot connect to the database.")
        print("Please start Docker Desktop and run: docker start salespilot-pg")
        sys.exit(1)

    from graph.build import build_graph
    graph = build_graph()

    # Resolve dynamic must_contain for Q1
    dormant_names = _get_dormant_names()

    passed = 0
    failed = 0

    print("End-to-End Eval")
    print("=" * 60)

    for case in CASES:
        # Fill in dynamic must_contain
        if case.get("must_contain_dynamic") == "dormant_names":
            case = dict(case, must_contain=dormant_names)

        result = graph.invoke({
            "question":        case["question"],
            "required_agents": [],
            "sql_result":      None,
            "rag_result":      None,
            "final_answer":    "",
        })

        ok, reasons = evaluate(case, result)

        status = "PASS" if ok else "FAIL"
        print(f"\n[{status}] {case['id']}: {case['question']}")
        if not ok:
            for r in reasons:
                print(f"       ✗ {r}")
            failed += 1
        else:
            passed += 1

    print("\n" + "=" * 60)
    print(f"Result: {passed}/{len(CASES)} passed", end="")
    if failed:
        print(f"  —  {failed} FAILED")
    else:
        print("  ✓  all correct")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
