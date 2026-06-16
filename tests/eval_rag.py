#!/usr/bin/env python3
"""
RAG retrieval eval — automatic accuracy check.

For each (question, expected_file) pair, asks ChromaDB for the top result
and checks whether the first chunk comes from the expected file.

Run from the project root:
  python tests/eval_rag.py
"""

import sys
from pathlib import Path

# Make sure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb

CHROMA_DIR = "chroma_db"
COLLECTION  = "contracts"

# ---------------------------------------------------------------------------
# Test cases: (question, expected_filename)
# These are NOT the 4 acceptance questions — different wording, same intent.
# ---------------------------------------------------------------------------
CASES = [
    (
        "What discount rate does Acme Corp receive on purchases?",
        "acme_corp_msa.md",
    ),
    (
        "What is the fixed contract price for PX-1000 under the Acme agreement?",
        "acme_corp_msa.md",
    ),
    (
        "What are the payment terms in Globex's contract?",
        "globex_msa.md",
    ),
    (
        "What discount is Initech entitled to?",
        "initech_msa.md",
    ),
    (
        "When does Umbrella Ltd's agreement renew?",
        "umbrella_ltd_msa.md",
    ),
    (
        "What are Soylent Systems' pricing terms?",
        "soylent_systems_msa.md",
    ),
]


def run_eval() -> None:
    client     = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_or_create_collection(COLLECTION)

    passed = 0
    failed = 0

    print("RAG Retrieval Eval")
    print("=" * 60)

    for question, expected_file in CASES:
        results = collection.query(
            query_texts=[question],
            n_results=1,
            include=["metadatas"],
        )
        top_file = results["metadatas"][0][0]["filename"]
        ok       = top_file == expected_file

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"\n[{status}] {question[:55]}...")
        print(f"       expected : {expected_file}")
        if not ok:
            print(f"       got      : {top_file}")

    print("\n" + "=" * 60)
    print(f"Result: {passed}/{len(CASES)} passed", end="")
    if failed:
        print(f"  —  {failed} FAILED ← check these")
    else:
        print("  ✓  all correct")


if __name__ == "__main__":
    run_eval()
