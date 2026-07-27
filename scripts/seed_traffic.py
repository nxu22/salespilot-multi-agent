#!/usr/bin/env python3
"""
Drive a realistic mix of questions at a running SalesPilot instance so the
Grafana panels have something to show.

The mix matters more than the volume. Questions are drawn from four buckets so
that every series on the dashboard moves:

  sql          exercises sql_agent and sql_query_duration_seconds
  rag          exercises rag_agent, retrieval_duration_seconds, retrieval_chunks
  cross        routes to both agents in parallel
  unanswerable drives refusals_total, which stays flat on a purely happy path

Run from the project root with the stack up:

  python scripts/seed_traffic.py
  python scripts/seed_traffic.py --url http://127.0.0.1:8000 --delay 2
"""

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS: list[tuple[str, str]] = [
    # ── structured data ────────────────────────────────────────────────────
    ("sql", "Which accounts haven't ordered in 90 days?"),
    ("sql", "Top 5 products by revenue this quarter?"),
    ("sql", "How many orders has Acme Corp placed in total?"),
    ("sql", "Which region has the most accounts?"),
    ("sql", "What is the average order value across all orders?"),
    ("sql", "List the 5 most expensive products in the catalog."),
    ("sql", "How many accounts are there per industry?"),
    ("sql", "Which products have never been ordered?"),
    ("sql", "What is total revenue over the last 30 days?"),
    ("sql", "Who are the top 5 accounts by total spend?"),
    ("sql", "How many orders were placed in the last 7 days?"),
    ("sql", "Which account has the most contacts on file?"),

    # ── contract documents ─────────────────────────────────────────────────
    ("rag", "What is the contract discount for Acme Corp?"),
    ("rag", "What payment terms does Globex specify in their contract?"),
    ("rag", "When does Umbrella Ltd's agreement renew?"),
    ("rag", "What discount is Initech entitled to?"),
    ("rag", "What are Soylent Systems' pricing terms?"),
    ("rag", "Does Acme's MSA include a termination clause?"),
    ("rag", "What are the renewal terms in Globex's contract?"),
    ("rag", "Is there a volume discount in Initech's agreement?"),
    ("rag", "What liability limits does Umbrella Ltd's contract set?"),
    ("rag", "What confidentiality terms appear in Soylent Systems' MSA?"),

    # ── both sources ───────────────────────────────────────────────────────
    ("cross", "Compare Acme's contract price vs catalog price for product PX-1000."),
    ("cross", "Compare Initech's contracted discount rate to the prices they actually paid."),
    ("cross", "Does Globex's order history line up with the terms in their contract?"),
    ("cross", "What is Umbrella Ltd's discount, and how many orders have they placed?"),

    # ── outside the dataset — these should be refused, not invented ────────
    ("unanswerable", "What's the weather like today?"),
    ("unanswerable", "Who is the CEO of Acme Corp?"),
    ("unanswerable", "What is our company's current stock price?"),
    ("unanswerable", "How many employees does Globex have?"),
    ("unanswerable", "What is the capital of France?"),
]

REFUSAL_MARKER = "could not find"


def ask(url: str, question: str, timeout: float) -> dict:
    payload = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000",
                        help="Base URL of the running API (default: %(default)s)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Seconds between questions (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Per-request timeout in seconds (default: %(default)s)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Passes over the question list (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=0,
                        help="Shuffle seed — fixed so runs are reproducible")
    parser.add_argument("--no-shuffle", action="store_true",
                        help="Ask in bucket order instead of interleaved")
    args = parser.parse_args()

    plan = QUESTIONS * args.repeat
    if not args.no_shuffle:
        # Interleave the buckets so the dashboard shows a realistic mix rather
        # than four flat blocks of one question type.
        plan = plan[:]
        random.Random(args.seed).shuffle(plan)

    print(f"Sending {len(plan)} questions to {args.url} "
          f"({args.delay}s apart)\n")

    counts = {"ok": 0, "refused": 0, "failed": 0}
    started = time.monotonic()

    for i, (bucket, question) in enumerate(plan, 1):
        try:
            result = ask(args.url, question, args.timeout)
            answer = (result.get("answer") or "").strip()
            refused = REFUSAL_MARKER in answer.lower()
            counts["refused" if refused else "ok"] += 1

            tables = ",".join(result.get("tables") or []) or "-"
            docs = ",".join(result.get("documents") or []) or "-"
            status = "REFUSED" if refused else "ok"
            print(f"[{i:>2}/{len(plan)}] {bucket:<12} {status:<8} "
                  f"tables={tables} docs={docs}")
        except urllib.error.URLError as exc:
            counts["failed"] += 1
            print(f"[{i:>2}/{len(plan)}] {bucket:<12} FAILED   {exc}")
        except Exception as exc:  # noqa: BLE001 - traffic generator, keep going
            counts["failed"] += 1
            print(f"[{i:>2}/{len(plan)}] {bucket:<12} FAILED   {type(exc).__name__}: {exc}")

        if i < len(plan):
            time.sleep(args.delay)

    elapsed = time.monotonic() - started
    print(f"\nDone in {elapsed:.0f}s — "
          f"{counts['ok']} answered, {counts['refused']} refused, "
          f"{counts['failed']} failed")

    if counts["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
