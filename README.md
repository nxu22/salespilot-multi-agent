# SalesPilot — Multi-Agent Sales Intelligence Assistant

SalesPilot lets sales teams ask questions about their customers in plain English and get answers pulled from two sources simultaneously — a PostgreSQL database and contract documents — with every factual claim annotated with its exact source.

> Built with LangGraph · Claude API · PostgreSQL · ChromaDB · FastAPI

---

## What it does

You type a question like *"Which accounts haven't ordered in 90 days?"* and SalesPilot:

1. Figures out whether the answer lives in the **database**, the **contracts**, or **both**
2. Queries the right source(s) automatically and in parallel
3. Returns a plain-English answer where **every number and fact cites its source**

If the data isn't there, it says so — it never fabricates.

---

## Example questions

| Question | Sources used |
|---|---|
| Which accounts haven't ordered in 90 days? | `orders` table, `accounts` table |
| What's the contract discount for Acme Corp? | `acme_corp_msa.md` |
| Top 5 products by revenue this quarter? | `products`, `order_items` tables |
| Compare Acme's contract price vs catalog price for PX-1000 | `products` table + `acme_corp_msa.md` |

---

## How it works — Architecture

```
User question
      │
      ▼
 Orchestrator          ← classifies intent, decides which agents to call
  (Claude Haiku)
      │
   ┌──┴──┐
   │     │  (parallel)
   ▼     ▼
SQL     RAG            ← agents run at the same time when both are needed
Agent   Agent
   │     │
   └──┬──┘
      ▼
 Synthesis             ← assembles grounded answer with source citations
  (Claude Haiku)
      │
      ▼
 Final answer
```

### The four nodes

**Orchestrator** — reads the question, decides whether to call the SQL agent, the RAG agent, or both. Uses Claude's tool-use API to output a structured routing decision.

**SQL Agent** — generates a PostgreSQL SELECT query, validates it (must be a single SELECT — no writes allowed), executes it through a read-only database role, and returns the rows with the table names it touched.

**RAG Agent** — searches a ChromaDB vector store of chunked contract documents, retrieves the 4 most relevant passages, and returns them with the filename of each source document.

**Synthesis** — receives whatever the agents returned and writes a grounded answer. Every claim must cite either a table name (`source: orders table`) or a document (`source: acme_corp_msa.md`). If no data was found, returns *"I could not find this in the available data."*

### Safety

SQL injection is blocked at two layers:
1. **Application layer** — sqlparse validates that the generated SQL is exactly one SELECT statement. Anything else (INSERT, DROP, UPDATE, multi-statement) is rejected before it reaches the database.
2. **Database layer** — the agent connects as `sp_readonly`, a role with SELECT-only grants. Even if validation were bypassed, writes are impossible at the database level.

---

## Project structure

```
salespilot/
├── graph/
│   ├── state.py          # AgentState TypedDict shared across all nodes
│   ├── orchestrator.py   # Intent classification → routing decision
│   ├── sql_agent.py      # NL → SQL → validation → execution
│   ├── rag_agent.py      # ChromaDB vector retrieval
│   ├── synthesis.py      # Grounded answer assembly
│   └── build.py          # Wires nodes + conditional edges into the graph
│
├── seed_data.py          # Creates DB tables, seeds sample data, writes contract docs
├── ingest_contracts.py   # Chunks contract_docs/ into ChromaDB
├── main.py               # CLI: python main.py "your question here"
├── api.py                # FastAPI server (POST /ask) + serves the chat UI
├── static/
│   └── index.html        # Chat UI (single HTML file, no build step)
│
└── tests/
    ├── eval_rag.py        # RAG retrieval accuracy eval (top-1 file match)
    └── eval_e2e.py        # End-to-end eval (answer correctness + source grounding)
```

---

## Setup

### Requirements

- Python 3.11+
- PostgreSQL (via Docker)
- API keys: Anthropic, Langfuse (optional — for tracing)

### 1. Clone and install

```bash
git clone https://github.com/nxu22/salespilot-multi-agent.git
cd salespilot-multi-agent
pip install langgraph langchain-anthropic anthropic psycopg2-binary \
            chromadb sqlparse fastapi uvicorn python-dotenv langfuse
```

### 2. Start PostgreSQL

```bash
docker run -d --name salespilot-pg \
  -e POSTGRES_USER=salespilot \
  -e POSTGRES_PASSWORD=salespilot \
  -e POSTGRES_DB=salespilot \
  -p 5433:5432 postgres:16
```

### 3. Create a `.env` file

```
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://salespilot:salespilot@localhost:5433/salespilot
SQL_AGENT_DATABASE_URL=postgresql://sp_readonly:readonly@localhost:5433/salespilot
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://us.cloud.langfuse.com
```

### 4. Seed the database and ingest contracts

```bash
python seed_data.py
python seed_data.py --verify     # sanity-check the dataset
python ingest_contracts.py --reset
```

### 5. Run

**Web UI (recommended)**
```bash
uvicorn api:app --port 8080
# Open http://localhost:8080
```

**CLI**
```bash
python main.py "Which accounts haven't ordered in 90 days?"
python main.py "What's the contract discount for Acme Corp?"
```

---

## Running the evals

```bash
# RAG retrieval accuracy (does the right contract file come back?)
python tests/eval_rag.py

# End-to-end answer correctness + source grounding (5 acceptance questions)
python tests/eval_e2e.py
```

Expected output:
```
End-to-End Eval
============================================================
[PASS] Q1: Which accounts haven't ordered in 90 days?
[PASS] Q2: What's the contract discount for Acme Corp?
[PASS] Q3: Top 5 products by revenue this quarter?
[PASS] Q4: Compare Acme's contract price vs catalog price for product PX-1000
[PASS] Q5: What's the weather like today?
============================================================
Result: 5/5 passed  ✓  all correct
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (Send API for parallel fan-out) |
| LLM | Anthropic Claude Haiku (via claude-haiku-4-5) |
| Structured data | PostgreSQL + psycopg2 |
| Vector search | ChromaDB (all-MiniLM-L6-v2 embeddings) |
| Tracing | Langfuse Cloud |
| API | FastAPI |
| Frontend | Vanilla HTML/CSS/JS (no framework) |

---

## Author

**Nan Xu** · [nanxu.site](https://nanxu.site) · [github.com/nxu22](https://github.com/nxu22)
