"""
Prometheus instrumentation for SalesPilot.

Lives at the repo root, alongside api.py (this repo is flat — no app/ package).
Import from anywhere as:  from metrics import track_agent, record_usage, ...

All metric objects are defined here and only here. Defining a Counter or
Histogram with the same name elsewhere raises at import time.

Usage:
    from metrics import track_agent, record_usage

    @track_agent("sql_agent")
    async def sql_agent_node(state):
        ...
        response = await client.messages.create(...)
        record_usage("sql_agent", "claude-haiku-4-5", response.usage)
        return state
"""

from __future__ import annotations

import functools
import inspect
import time
from contextlib import contextmanager
from typing import Any

from prometheus_client import Counter, Histogram

# --------------------------------------------------------------------------
# Pricing — USD per 1M tokens. Add entries here if more models get used.
# Cost is tracked as its own counter so the Grafana panel is a plain rate(),
# instead of doing price arithmetic in PromQL.
# --------------------------------------------------------------------------
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
_DEFAULT_PRICE = {"input": 0.0, "output": 0.0}


# --------------------------------------------------------------------------
# Metric definitions
# --------------------------------------------------------------------------
# Buckets tuned for LLM latency. Prometheus defaults top out at 10s and are
# far too dense below 1s, which would push every agent observation toward the
# upper buckets and make P95 meaningless.
_AGENT_BUCKETS = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0)

agent_duration_seconds = Histogram(
    "agent_duration_seconds",
    "Wall-clock execution time of a single agent node",
    labelnames=("agent",),
    buckets=_AGENT_BUCKETS,
)

agent_requests_total = Counter(
    "agent_requests_total",
    "Number of agent node executions",
    labelnames=("agent", "status"),  # status: success | error
)

agent_errors_total = Counter(
    "agent_errors_total",
    "Agent node failures by exception type",
    labelnames=("agent", "error_type"),
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM tokens consumed",
    labelnames=("agent", "model", "type"),  # type: input | output
)

llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "Estimated LLM spend in USD, derived from token counts and MODEL_PRICING",
    labelnames=("agent", "model"),
)

sql_query_duration_seconds = Histogram(
    "sql_query_duration_seconds",
    "Execution time of the SQL issued by the SQL agent (database time only)",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

retrieval_duration_seconds = Histogram(
    "retrieval_duration_seconds",
    "Vector similarity search latency (ChromaDB + Voyage embedding call)",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

retrieval_chunks = Histogram(
    "retrieval_chunks",
    "Number of chunks returned per retrieval call",
    buckets=(1, 2, 3, 5, 8, 13, 21),
)

refusals_total = Counter(
    "refusals_total",
    "Times the agent declined to answer because sources were insufficient",
    labelnames=("agent",),
)


# --------------------------------------------------------------------------
# Token / cost recording
# --------------------------------------------------------------------------
def _extract_usage(usage: Any) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) from an Anthropic or OpenAI usage object."""
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        get = usage.get
    else:
        get = lambda k, d=None: getattr(usage, k, d)  # noqa: E731

    prompt = get("input_tokens") or get("prompt_tokens") or 0
    completion = get("output_tokens") or get("completion_tokens") or 0
    return int(prompt), int(completion)


def record_usage(agent: str, model: str, usage: Any) -> None:
    """
    Record token counts and estimated cost for one LLM call.

    Call this wherever usage is already reported to Langfuse — same data,
    two destinations. `usage` accepts an Anthropic/OpenAI usage object or a dict.
    """
    input_tokens, output_tokens = _extract_usage(usage)
    if not (input_tokens or output_tokens):
        return

    llm_tokens_total.labels(agent=agent, model=model, type="input").inc(input_tokens)
    llm_tokens_total.labels(agent=agent, model=model, type="output").inc(output_tokens)

    price = MODEL_PRICING.get(model, _DEFAULT_PRICE)
    cost = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
    if cost:
        llm_cost_usd_total.labels(agent=agent, model=model).inc(cost)


def record_refusal(agent: str) -> None:
    """Count a source-insufficient refusal — a designed behaviour, so make it visible."""
    refusals_total.labels(agent=agent).inc()


# --------------------------------------------------------------------------
# Agent instrumentation
# --------------------------------------------------------------------------
def track_agent(agent_name: str):
    """
    Decorator for a LangGraph node (or any agent function).

    Records duration, success/error count and exception type without touching
    the body of the function. Works on both sync and async callables.
    """

    def decorator(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    agent_errors_total.labels(
                        agent=agent_name, error_type=type(exc).__name__
                    ).inc()
                    agent_requests_total.labels(agent=agent_name, status="error").inc()
                    raise
                else:
                    agent_requests_total.labels(agent=agent_name, status="success").inc()
                    return result
                finally:
                    agent_duration_seconds.labels(agent=agent_name).observe(
                        time.perf_counter() - start
                    )

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                agent_errors_total.labels(
                    agent=agent_name, error_type=type(exc).__name__
                ).inc()
                agent_requests_total.labels(agent=agent_name, status="error").inc()
                raise
            else:
                agent_requests_total.labels(agent=agent_name, status="success").inc()
                return result
            finally:
                agent_duration_seconds.labels(agent=agent_name).observe(
                    time.perf_counter() - start
                )

        return sync_wrapper

    return decorator


@contextmanager
def track_block(histogram: Histogram, **labels):
    """
    Time an inner block — the raw DB call, the vector search.

    Example:
        with track_block(sql_query_duration_seconds):
            rows = engine.execute(query).fetchall()
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        target = histogram.labels(**labels) if labels else histogram
        target.observe(time.perf_counter() - start)
