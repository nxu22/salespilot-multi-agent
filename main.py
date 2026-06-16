import argparse
import sys
sys.stdout.reconfigure(encoding="utf-8")

from langfuse import observe
from graph.build import build_graph

VALID_AGENTS = {"sql", "rag"}


def main() -> None:
    parser = argparse.ArgumentParser(description="SalesPilot — multi-agent sales assistant")
    parser.add_argument("question", help="Natural language question")
    parser.add_argument(
        "--route",
        default="sql",
        metavar="AGENTS",
        help="Comma-separated agents to route to: sql, rag, or sql,rag  (default: sql)",
    )
    args = parser.parse_args()

    required_agents = [a.strip() for a in args.route.split(",") if a.strip()]
    unknown = set(required_agents) - VALID_AGENTS
    if unknown:
        parser.error(f"Unknown agent(s): {unknown}. Valid values: sql, rag")

    graph = build_graph()

    @observe(name=args.question)
    def run():
        return graph.invoke({
            "question":        args.question,
            "required_agents": required_agents,
            "sql_result":      None,
            "rag_result":      None,
            "final_answer":    "",
        })

    result = run()

    print(f"\nAnswer: {result['final_answer']}")


if __name__ == "__main__":
    main()
