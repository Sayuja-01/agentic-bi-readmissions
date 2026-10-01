"""
Run the full agentic BI pipeline end to end.

Usage:
    python -m src.main "Which hospitals are most exposed to readmission penalties, and what's driving it?"

If no question is given, a default seed question is used.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

from dotenv import load_dotenv
from anthropic import Anthropic

from src.graph import build_graph
from src.logger import ReasoningTrace

DEFAULT_QUESTION = "Which hospitals are most exposed to readmission penalties, and what's driving it?"
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "hospital_readmissions.db")


def print_memo(memo: dict):
    print("\n" + "=" * 70)
    print("STRATEGIST MEMO")
    print("=" * 70)
    print("\nSITUATION\n" + memo.get("situation", "(missing)"))
    print("\nKEY FINDINGS")
    for f in memo.get("key_findings", []):
        print(f"  - {f}")
    print("\nRECOMMENDATION\n" + memo.get("recommendation", "(missing)"))
    print("=" * 70)


def main():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found. Check your .env file.")
        sys.exit(1)

    seed_question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    print(f"Seed question: {seed_question}\n")

    client = Anthropic(api_key=api_key)
    conn = sqlite3.connect(DB_PATH)
    trace = ReasoningTrace()

    graph = build_graph(client, conn)

    initial_state = {
        "seed_question": seed_question,
        "trace": trace,
        "error": None,
    }

    print("Running investigation...")
    final_state = graph.invoke(initial_state)
    conn.close()

    if final_state.get("error"):
        print(f"\nInvestigation stopped early: {final_state['error']}")
    else:
        print(f"\nPlanner produced {len(final_state['subquestions'])} sub-questions.")
        for sr in final_state["sub_results"]:
            status = "OK" if sr["exec_result"] else f"FAILED ({sr['exec_error']})"
            print(f"  [{sr['category']}] {sr['sub_question']} -> {status}")

        print(f"\n{len(final_state['findings'])} findings synthesized.")
        print_memo(final_state["memo"])

    os.makedirs("outputs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    trace_path = trace.save()
    print(f"\nFull reasoning trace saved to: {trace_path}")

    if not final_state.get("error"):
        memo_path = os.path.join("outputs", f"memo_{timestamp}.md")
        with open(memo_path, "w") as f:
            f.write(f"# Readmission Exposure Analysis\n\n**Seed question:** {seed_question}\n\n")
            f.write(f"## Situation\n{final_state['memo'].get('situation', '')}\n\n")
            f.write("## Key findings\n")
            for kf in final_state['memo'].get('key_findings', []):
                f.write(f"- {kf}\n")
            f.write(f"\n## Recommendation\n{final_state['memo'].get('recommendation', '')}\n")
        print(f"Memo saved to: {memo_path}")


if __name__ == "__main__":
    main()
