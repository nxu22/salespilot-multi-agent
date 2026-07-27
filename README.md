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
├── metrics.py            # All Prometheus metric objects + instrumentation helpers
├── seed_data.py          # Creates DB tables, seeds sample data, writes contract docs
├── ingest_contracts.py   # Chunks contract_docs/ into ChromaDB
├── main.py               # CLI: python main.py "your question here"
├── api.py                # FastAPI server (POST /ask, GET /metrics) + chat UI
├── static/
│   └── index.html        # Chat UI (single HTML file, no build step)
│
├── observability/
│   ├── prometheus.yml    # Scrape config
│   ├── alerts.yml        # Alert rules
│   └── grafana/          # Provisioned datasource + dashboard JSON
│
├── scripts/
│   └── seed_traffic.py   # Sends a mixed question load so the panels have data
│
├── Dockerfile            # API image
├── docker-compose.yml    # api + prometheus + grafana
│
└── tests/
    ├── eval_rag.py        # RAG retrieval accuracy eval (top-1 file match)
    └── eval_e2e.py        # End-to-end eval (answer correctness + source grounding)
```

---

## Setup

### Requirements

- Python 3.11+
- Docker (PostgreSQL, and the observability stack)
- API keys: **Anthropic**, **Voyage AI** (embeddings — the app will not start
  without it), Langfuse (optional — for tracing)

### 1. Clone and install

```bash
git clone https://github.com/nxu22/salespilot-multi-agent.git
cd salespilot-multi-agent
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
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

Copy [`.env.example`](.env.example) to `.env` and fill it in:

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...          # dashboard.voyageai.com → API keys
DATABASE_URL=postgresql://salespilot:salespilot@localhost:5433/salespilot
SQL_AGENT_DATABASE_URL=postgresql://sp_readonly:readonly@localhost:5433/salespilot
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://us.cloud.langfuse.com
GRAFANA_USER=admin
GRAFANA_PASSWORD=...
```

### 4. Seed the database and ingest contracts

```bash
python seed_data.py --drop --verify   # rebuild the dataset, then check it
python ingest_contracts.py --reset
```

Order dates are generated relative to the day you seed, so questions like
*"this quarter"* go stale over time — re-run the seed if the evals start
failing on date-bounded questions. `--verify` on its own is read-only.

Re-seeding rewrites `contract_docs/`, so `ingest_contracts.py --reset` has to
follow it — the index and the documents must be built from the same source.

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

## Observability

Two layers, kept deliberately separate because they answer different questions.

| | Prometheus + Grafana | Langfuse |
|---|---|---|
| **Answers** | Is the system healthy, and what is it costing? | Why did *this one* request behave that way? |
| **Granularity** | Aggregate — histograms and rates over time | Per-request — one trace per question |
| **Use it to** | Spot a bottleneck, watch spend, fire an alert | Replay a specific answer step by step |
| **Analogy** | The monthly spending chart | The individual receipt |

Neither replaces the other. You cannot get a P95 across ten thousand requests by
opening traces one at a time, and you cannot recover what a single user asked
from a histogram.

### Metrics exposed

`GET /metrics` serves the Prometheus exposition format. Everything is defined in
one place — [`metrics.py`](metrics.py) — and labels are kept to bounded values
(`agent`, `model`, `type`, `error_type`); nothing is labelled with a question,
a user, or a session.

| Metric | Type | What it answers |
|---|---|---|
| `agent_duration_seconds` | histogram | Which of the four nodes is the bottleneck |
| `agent_requests_total` | counter | Throughput per node, split success/error |
| `agent_errors_total` | counter | Which node fails, and with which exception |
| `llm_tokens_total` | counter | Input/output token ratio per node — the prompt-caching signal |
| `llm_cost_usd_total` | counter | Spend per node, derived from tokens and `MODEL_PRICING` |
| `sql_query_duration_seconds` | histogram | Database time, isolated from the surrounding LLM time |
| `retrieval_duration_seconds` | histogram | Vector search **plus** the Voyage embedding round-trip |
| `retrieval_chunks` | histogram | Chunks returned per query — drops to zero if the index breaks |
| `refusals_total` | counter | How often the system declines for lack of sources |

Two notes worth knowing before reading the numbers:

- **`retrieval_duration_seconds` is dominated by the network.** The Voyage
  embedding call is a remote round-trip; the local vector search over ~240 chunks
  is sub-millisecond. This metric measures the provider far more than ChromaDB.
- **`llm_cost_usd_total` covers Anthropic only.** Voyage embedding spend is not
  tracked — it is negligible next to generation cost at this scale.

### Bringing the stack up

```bash
docker compose up -d          # api + prometheus + grafana
```

| Service | URL | Notes |
|---|---|---|
| API | http://127.0.0.1:8000 | chat UI and `POST /ask` |
| Prometheus | http://127.0.0.1:9090 | Status → Targets to confirm scraping |
| Grafana | http://127.0.0.1:3000 | credentials from `.env` |

Every port is bound to `127.0.0.1`, so nothing is reachable off the machine.
Grafana's admin credentials come from `GRAFANA_USER` / `GRAFANA_PASSWORD` in
`.env` — compose refuses to start if they are unset.

Postgres is deliberately **not** in `docker-compose.yml`. The seeded data lives
in the standalone `salespilot-pg` container from the setup step above, so the
API reaches it over `host.docker.internal` rather than a compose network.

The dashboard and its Prometheus datasource are provisioned from
[`observability/`](observability/), so they are rebuilt from source on every
start rather than clicked together in the UI.

### Generating traffic

A fresh dashboard is an empty dashboard. This sends a realistic mix — SQL-heavy,
RAG-heavy, cross-source, and deliberately unanswerable questions so the refusal
counter moves too:

```bash
python scripts/seed_traffic.py
```

![Grafana dashboard](docs/images/grafana-dashboard.png)

### Alerts

Eight rules in [`observability/alerts.yml`](observability/alerts.yml) cover
availability, per-agent error rate, latency, retrieval health, refusal rate, and
spend. One of them is less obvious than the rest: `UnpricedModelInUse` fires when
a model records tokens but no cost — the signature of a model id missing from
`MODEL_PRICING`, which otherwise under-reports spend silently.

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
| Vector search | ChromaDB + Voyage AI embeddings (`voyage-3.5-lite`) |
| Tracing | Langfuse Cloud |
| Metrics & alerting | Prometheus + Grafana |
| API | FastAPI |
| Frontend | Vanilla HTML/CSS/JS (no framework) |

---

## Author

**Nan Xu** · [nanxu.site](https://nanxu.site) · [github.com/nxu22](https://github.com/nxu22)
