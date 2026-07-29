# SalesPilot

**Ask your sales data a question in plain English. Get an answer where every number cites the table or contract it came from.**

SalesPilot routes each question across a PostgreSQL database and a set of contract documents — in parallel when both are needed — and refuses to answer when the data isn't there.

[**Live demo →**](https://salespilot-multi-agent.onrender.com) &nbsp;·&nbsp; LangGraph · Claude Haiku 4.5 · PostgreSQL · ChromaDB · FastAPI

<sub>The demo runs on a free-tier instance that sleeps — the first request can take ~60s to wake it. It also predates the observability work below, so `/metrics` returns 404 there; the dashboard figures come from a local stack.</sub>

<!-- TODO: record a 10-15s GIF of the chat UI answering a cross-source question -> docs/images/demo.gif -->

```
"Compare Acme's contract price vs catalog price for PX-1000"

→ Acme pays $1,100.00 under the MSA against a $1,250.00 catalog price — a 12% discount.
   source: products table · source: acme_corp_msa.md
```

---

## What measuring it actually changed

The project is instrumented end to end (Prometheus + Grafana for aggregate health, Langfuse for per-request traces). The point wasn't the dashboard — it was that two optimisations that looked obvious beforehand turned out to be worthless.

| Measured | Value | What follows from it |
|---|---|---|
| Database share of `sql_agent` time | **2.6%** | The other ~97% is Claude turning the question into SQL. Indexes, query tuning, connection pooling — none of it would move the needle. The cost is generation, not execution. |
| Retrieval share of `rag_agent` time | **99.9%** | The node does almost nothing but wait on a remote Voyage embedding call. Local vector search over ~240 chunks is sub-millisecond, so `TOP_K` and ChromaDB are not the lever — caching query embeddings or moving the model in-process is. |
| `orchestrator` input/output token ratio | **22.4** | A fixed system prompt plus tool schema in, a two-token routing decision out. This looked like the textbook case for prompt caching. It isn't — see below. |
| Cost per question | **$0.0023** | ≈ $2.33 per thousand questions on Haiku 4.5, across all three model calls. |
| Refusal rate | **22.6%** | Matches the deliberately-unanswerable share of generated traffic — intended behaviour. The counter exists so drift in this number is visible. |

The database was the obvious suspect for SQL latency. `TOP_K` was the obvious knob for retrieval latency. Neither would have helped, and I'd have spent a day finding that out by hand.

### The third one the instrumentation got wrong

A 22.4 ratio against a prefix that never changes is what prompt caching exists for, so that became the next change. It doesn't work here, and the dashboard could not have told me why.

Caching has a **minimum cacheable prefix**, and it varies by model — 512 tokens on the newest models, 1024 on most, and **4096 on Haiku 4.5**, which is what this runs. Below the minimum nothing caches: no error, no warning, `cache_creation_input_tokens` simply comes back `0`.

The orchestrator's cacheable prefix is its tool schema plus system prompt — [`graph/orchestrator.py:9-44`](graph/orchestrator.py) — and that is **1,629 characters**. A token is at minimum one character, so even at the impossible ceiling of one token per character the prefix is 1,629 tokens against a 4,096 threshold. Realistically it is around 407, which is also what the measured 22.4 ratio implies. It is short by roughly 10x, and no current model's minimum is low enough to close that.

The ceiling was never high either. Cache reads bill at 0.1x input and writes at 1.25x, so a perfectly cached orchestrator prefix was worth about $0.0002 of the $0.0023 per question — under 10%.

The ratio was real and the reasoning from it was sound. It was just the wrong quantity: **caching keys off absolute prefix length, and a ratio cannot see that.** Both numbers come from `llm_tokens_total`, but only one of them decides whether the feature applies, and the panel showed the other. The instrumentation earned its keep twice by killing optimizations before I built them; this is the case where it pointed at one and I still had to go read the provider's constraints to find out it was unavailable.

What it did leave behind is a real fix: `_extract_usage` in [`metrics.py`](metrics.py) read only `input_tokens`, which under caching is just the *uncached remainder*. Had caching ever been switched on anywhere, token counts and spend would have under-reported silently. That is now handled, along with the 1.25x/0.1x cache rates — the same class of bug as `UnpricedModelInUse`, caught by reading the billing model rather than by a failure.

> Measured over 31 requests against a local instance. P95 values are indicative only at this sample size; the ratios (time share, token ratio) are stable. [`docs/promql.md`](docs/promql.md) has the query behind every figure — including the aggregation mistake that reports a 3.9% retrieval share against a true 99.9%.

![Grafana dashboard: cost per question, refusal rate, P95 latency by agent, time share per agent](docs/images/grafana-dashboard.png)

The screenshot is regenerated by command rather than captured by hand, so it can't drift from the dashboard definition.

---

## Architecture

```
User question
      │
      ▼
 Orchestrator          ← classifies intent, decides which agents to call
      │
   ┌──┴──┐
   │     │  (parallel — LangGraph Send API)
   ▼     ▼
 SQL     RAG
Agent   Agent
   │     │
   └──┬──┘
      ▼
 Synthesis             ← grounded answer, every claim carries a source
      │
      ▼
 Final answer
```

**Orchestrator** — reads the question and emits a structured routing decision via Claude's tool-use API: SQL, RAG, or both.

**SQL Agent** — generates a PostgreSQL `SELECT`, validates it, executes it through a read-only role, returns rows plus the tables it touched.

**RAG Agent** — searches a ChromaDB store of chunked contracts, returns the 4 most relevant passages with their source filenames.

**Synthesis** — assembles the answer. Every claim must cite a table (`source: orders table`) or a document (`source: acme_corp_msa.md`). With no data, it returns *"I could not find this in the available data."* rather than guessing.

### Example questions

| Question | Sources used |
|---|---|
| Which accounts haven't ordered in 90 days? | `orders`, `accounts` |
| What's the contract discount for Acme Corp? | `acme_corp_msa.md` |
| Top 5 products by revenue this quarter? | `products`, `order_items` |
| Compare Acme's contract price vs catalog price for PX-1000 | `products` + `acme_corp_msa.md` |

---

## SQL injection is blocked at two layers

An LLM writing SQL is an injection surface, so validation alone isn't enough.

1. **Application layer** — `sqlparse` verifies the generated SQL is exactly one `SELECT`. Anything else (`INSERT`, `DROP`, `UPDATE`, multi-statement) is rejected before it reaches the database.
2. **Database layer** — the agent connects as `sp_readonly`, a role with `SELECT`-only grants. Even with validation bypassed, writes are impossible.

Metric labels are kept to bounded values (`agent`, `model`, `type`, `error_type`) — nothing is labelled with a question, user, or session. Every service port binds to `127.0.0.1`.

---

## Evals

Two suites, run before any commit that touches the graph:

```bash
python tests/eval_rag.py   # retrieval accuracy — does the right contract file come back?
python tests/eval_e2e.py   # answer correctness + source grounding, 5 acceptance questions
```

```
[PASS] Q1: Which accounts haven't ordered in 90 days?
[PASS] Q2: What's the contract discount for Acme Corp?
[PASS] Q3: Top 5 products by revenue this quarter?
[PASS] Q4: Compare Acme's contract price vs catalog price for PX-1000
[PASS] Q5: What's the weather like today?          ← must refuse
Result: 5/5 passed
```

Q5 is the one that matters. A system that answers it is a system that will invent a discount rate.

---

## Limitations, honestly

- **The dashboard figures come from 31 requests.** Ratios are trustworthy at that sample size; P95 latencies are not. Treat them as directional.
- **Seed data expires.** `seed_data.py` generates order dates relative to the seeding day, so date-bounded questions ("this quarter") go stale and evals degrade. Re-seed before drawing conclusions.
- **Cost tracking covers Anthropic only.** Voyage embedding spend isn't metered — negligible against generation at this scale, but it's a gap, not an absence.
- **Prompt caching doesn't apply.** The orchestrator's prefix is ~10x shorter than Haiku 4.5's 4096-token minimum, so the win the 22.4 ratio implied isn't collectable. Token accounting now handles the cache fields regardless.
- **The evals aren't wired to CI.** They're run by hand, which means "green" is a claim about the last time someone remembered to run them.
- **Single-tenant.** No auth, no per-user isolation. It's a demonstration of the agent architecture, not a product.

---

<details>
<summary><b>Setup</b> — clone, seed, run</summary>

### Requirements

Python 3.11+, Docker, and API keys for Anthropic, Voyage AI (embeddings — the app won't start without it), and optionally Langfuse.

### 1. Install

```bash
git clone https://github.com/nxu22/salespilot-multi-agent.git
cd salespilot-multi-agent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv/Scripts/activate
pip install -r requirements.txt
```

### 2. Start PostgreSQL

```bash
docker run -d --name salespilot-pg \
  -e POSTGRES_USER=salespilot -e POSTGRES_PASSWORD=salespilot \
  -e POSTGRES_DB=salespilot -p 5433:5432 postgres:16
```

### 3. Configure

Copy `.env.example` to `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
DATABASE_URL=postgresql://salespilot:salespilot@localhost:5433/salespilot
SQL_AGENT_DATABASE_URL=postgresql://sp_readonly:readonly@localhost:5433/salespilot
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://us.cloud.langfuse.com
GRAFANA_USER=admin
GRAFANA_PASSWORD=...
GRAFANA_RENDERER_TOKEN=...
```

### 4. Seed and ingest

```bash
python seed_data.py --drop --verify
python ingest_contracts.py --reset
```

Re-seeding rewrites `contract_docs/`, so `ingest_contracts.py --reset` has to follow it — the index and the documents must be built from the same source. `--verify` on its own is read-only.

### 5. Run

```bash
uvicorn api:app --port 8000     # then open http://localhost:8000
python main.py "Which accounts haven't ordered in 90 days?"   # or CLI
```

</details>

<details>
<summary><b>Observability</b> — the two layers and what each is for</summary>

|  | Prometheus + Grafana | Langfuse |
|---|---|---|
| Answers | Is the system healthy, and what is it costing? | Why did *this* request behave that way? |
| Granularity | Aggregate — histograms and rates over time | Per-request — one trace per question |
| Analogy | The monthly spending chart | The individual receipt |

Neither replaces the other. You can't get a P95 across ten thousand requests by opening traces one at a time, and you can't recover what one user asked from a histogram.

### Metrics

`GET /metrics` serves the Prometheus exposition format. Everything is defined in `metrics.py`.

| Metric | Type | What it answers |
|---|---|---|
| `agent_duration_seconds` | histogram | Which of the four nodes is the bottleneck |
| `agent_requests_total` | counter | Throughput per node, split success/error |
| `agent_errors_total` | counter | Which node fails, and with which exception |
| `llm_tokens_total` | counter | Tokens per node, split `input` / `output` / `cache_write` / `cache_read` |
| `llm_cost_usd_total` | counter | Spend per node, derived from tokens and `MODEL_PRICING` |
| `sql_query_duration_seconds` | histogram | Database time, isolated from surrounding LLM time |
| `retrieval_duration_seconds` | histogram | Vector search plus the Voyage embedding round-trip |
| `retrieval_chunks` | histogram | Chunks per query — drops to zero if the index breaks |
| `refusals_total` | counter | How often the system declines for lack of sources |

### Bringing the stack up

```bash
docker compose up -d            # api + prometheus + grafana + image renderer
python scripts/seed_traffic.py  # SQL-heavy, RAG-heavy, cross-source, and unanswerable questions
```

| Service | URL |
|---|---|
| API | http://127.0.0.1:8000 |
| Prometheus | http://127.0.0.1:9090 |
| Grafana | http://127.0.0.1:3000 |

Postgres is deliberately outside `docker-compose.yml` — the seeded data lives in the standalone `salespilot-pg` container, so the API reaches it over `host.docker.internal`. The dashboard and its datasource are provisioned from `observability/`, rebuilt from source on every start rather than clicked together in the UI.

Regenerating the screenshot:

```bash
curl -s -u "$GRAFANA_USER:$GRAFANA_PASSWORD" -o docs/images/grafana-dashboard.png \
  "http://127.0.0.1:3000/render/d/salespilot-overview/salespilot?from=now-6h&to=now&width=2400&height=1400&theme=dark&kiosk=true"
```

### Alerts

Eight rules in `observability/alerts.yml` cover availability, per-agent error rate, latency, retrieval health, refusal rate, and spend. The least obvious: `UnpricedModelInUse` fires when a model records tokens but no cost — the signature of a model id missing from `MODEL_PRICING`, which otherwise under-reports spend silently.

</details>

<details>
<summary><b>Project structure</b></summary>

```
salespilot/
├── graph/
│   ├── state.py            # AgentState TypedDict shared across all nodes
│   ├── orchestrator.py     # Intent classification → routing decision
│   ├── sql_agent.py        # NL → SQL → validation → execution
│   ├── rag_agent.py        # ChromaDB vector retrieval
│   ├── synthesis.py        # Grounded answer assembly
│   └── build.py            # Wires nodes + conditional edges
├── api.py                  # FastAPI (POST /ask, GET /metrics) + chat UI
├── main.py                 # CLI entry point
├── metrics.py              # All Prometheus metrics + tracking helpers
├── static/index.html       # Chat UI, single file, no build step
├── seed_data.py            # Creates and seeds the DB, writes contract docs
├── ingest_contracts.py     # Chunks contract_docs/ into ChromaDB via Voyage
├── observability/          # prometheus.yml, alerts.yml, provisioned Grafana
├── scripts/seed_traffic.py # Mixed question load
├── docs/promql.md          # The query behind every figure quoted above
├── tests/                  # eval_rag.py, eval_e2e.py
├── Dockerfile              # API image
├── docker-compose.yml      # api + prometheus + grafana + image renderer
└── .env.example            # Every variable the stack expects
```

Not in git, created locally: `.env`, `chroma_db/` (rebuilt by `ingest_contracts.py`), `contract_docs/` (rewritten by `seed_data.py`).

</details>

---

## Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (Send API for parallel fan-out) |
| LLM | Claude Haiku 4.5 |
| Structured data | PostgreSQL + psycopg2 |
| Vector search | ChromaDB + Voyage AI (`voyage-3.5-lite`) |
| Tracing | Langfuse Cloud |
| Metrics & alerting | Prometheus + Grafana |
| API | FastAPI |
| Frontend | Vanilla HTML/CSS/JS, no build step |

---

**Nan Xu** · [nanxu.site](https://nanxu.site) · [github.com/nxu22](https://github.com/nxu22) · [LinkedIn](https://www.linkedin.com/in/n-xu)
