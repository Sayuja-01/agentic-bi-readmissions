"""
LangGraph orchestration.

State flows: planner -> sql_stage -> synthesizer -> strategist -> END
After every node, a conditional edge checks state["error"]: if a node failed,
the graph routes straight to END instead of continuing (a "fail fast, report
honestly" behavior) rather than pushing broken data forward through the rest
of the pipeline.

Retries WITHIN a single node (e.g. SQL generator fixing its own broken query,
or the JSON parser fixing malformed output) are handled inside that node's own
function in nodes.py — that's a deliberate design choice, not an omission:
LangGraph's edges express node-to-node routing; retrying a sub-step several
times inside one node's own logic is ordinary Python control flow, and modeling
every one of those retries as its own graph edge would make the graph itself
harder to read without adding anything real.
"""

from typing import TypedDict, Optional, List, Dict, Any

from langgraph.graph import StateGraph, END

from src.nodes import planner_node, sql_stage_node, synthesizer_node, strategist_node
from src.logger import ReasoningTrace


class AgentState(TypedDict, total=False):
    seed_question: str
    subquestions: List[Dict[str, Any]]
    sub_results: List[Dict[str, Any]]
    results_for_prompt: List[str]
    findings: List[Dict[str, Any]]
    memo: Dict[str, Any]
    trace: ReasoningTrace
    error: Optional[str]


def route_on_error(state: AgentState) -> str:
    return "end" if state.get("error") else "continue"


def build_graph(client, conn):
    graph = StateGraph(AgentState)

    graph.add_node("planner", lambda state: planner_node(state, client))
    graph.add_node("sql_stage", lambda state: sql_stage_node(state, client, conn))
    graph.add_node("synthesizer", lambda state: synthesizer_node(state, client))
    graph.add_node("strategist", lambda state: strategist_node(state, client))

    graph.set_entry_point("planner")

    graph.add_conditional_edges("planner", route_on_error, {"continue": "sql_stage", "end": END})
    graph.add_conditional_edges("sql_stage", route_on_error, {"continue": "synthesizer", "end": END})
    graph.add_conditional_edges("synthesizer", route_on_error, {"continue": "strategist", "end": END})
    graph.add_edge("strategist", END)

    return graph.compile()
