import os

import chromadb
import chromadb.utils.embedding_functions as ef
from langfuse import observe

from graph.state import AgentState

_embed_fn   = ef.VoyageAIEmbeddingFunction(
    api_key=os.environ["VOYAGE_API_KEY"],
    model_name="voyage-3-lite",
)
_client     = chromadb.PersistentClient(path="chroma_db")
_collection = _client.get_or_create_collection("contracts", embedding_function=_embed_fn)

TOP_K = 4


@observe()
def rag_agent_node(state: AgentState) -> dict:
    print(f"[rag_agent]     retrieving chunks for: '{state['question']}'")

    results = _collection.query(
        query_texts=[state["question"]],
        n_results=TOP_K,
        include=["documents", "metadatas"],
    )

    chunks  = results["documents"][0]   # list of text strings
    metas   = results["metadatas"][0]   # list of metadata dicts
    sources = [m["filename"] for m in metas]

    print(f"[rag_agent]     retrieved {len(chunks)} chunks from: {sources}")

    return {"rag_result": {"chunks": chunks, "sources": sources}}
