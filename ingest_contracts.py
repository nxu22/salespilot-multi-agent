#!/usr/bin/env python3
"""
Chunk contract_docs/*.md by ## section headers and load into ChromaDB.
Run once before using the RAG agent:  python ingest_contracts.py
"""

import re
from pathlib import Path

import chromadb

DOCS_DIR    = Path("contract_docs")
CHROMA_DIR  = "chroma_db"
COLLECTION  = "contracts"


def _chunk_markdown(text: str, filename: str) -> list[dict]:
    """Split a markdown file on ## headers. Returns list of chunk dicts."""
    # Split on lines that start with ##
    parts = re.split(r"(?m)^(## .+)$", text)

    chunks = []

    # parts[0] is any text before the first ## (the file header / ## level 1)
    # then alternating: section_title, section_body, section_title, section_body …
    preamble = parts[0].strip()
    if preamble:
        chunks.append({
            "text":          preamble,
            "section_title": "preamble",
            "filename":      filename,
            "account_name":  _account_name(filename),
        })

    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip()          # "## Section 2: Pricing and Discounts"
        body  = parts[i + 1].strip()      # the text under that heading
        if body:
            chunks.append({
                "text":          f"Account: {_account_name(filename)}\n{title}\n\n{body}",
                "section_title": title.lstrip("# ").strip(),
                "filename":      filename,
                "account_name":  _account_name(filename),
            })

    return chunks


def _account_name(filename: str) -> str:
    """Derive a human-readable account name from the doc filename."""
    stem = filename.replace("_msa.md", "")
    return stem.replace("_", " ").title()


def ingest(reset: bool = False) -> None:
    client     = chromadb.PersistentClient(path=CHROMA_DIR)

    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass

    collection = client.get_or_create_collection(COLLECTION)

    md_files = sorted(DOCS_DIR.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No markdown files found in {DOCS_DIR}/")

    all_chunks = []
    for path in md_files:
        text   = path.read_text(encoding="utf-8")
        chunks = _chunk_markdown(text, path.name)
        all_chunks.extend(chunks)

    # ChromaDB needs unique IDs
    ids       = [f"{c['filename']}::{i}"    for i, c in enumerate(all_chunks)]
    documents = [c["text"]                   for c in all_chunks]
    metadatas = [
        {
            "filename":      c["filename"],
            "account_name":  c["account_name"],
            "section_title": c["section_title"],
        }
        for c in all_chunks
    ]

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    print(f"Ingested {len(all_chunks)} chunks from {len(md_files)} files into '{COLLECTION}'")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop and rebuild the collection")
    args = parser.parse_args()
    ingest(reset=args.reset)
