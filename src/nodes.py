"""
The four reasoning nodes: planner, sql stage (generator + executor with retry),
synthesizer, strategist. Each one mirrors exactly what we proved out in the
browser artifact — same prompts, same retry behavior, same JSON contracts.
"""

import json
import re
import sqlite3

from anthropic import Anthropic

from src.prompts import SYSTEM_PLANNER, SYSTEM_SQLGEN, SYSTEM_SYNTH, SYSTEM_STRATEGIST

MODEL = "claude-sonnet-5"


# ---------- low-level helpers ----------

def call_claude(client: Anthropic, system: str, user_text: str, max_tokens: int = 1000) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"```$", "", t)
    return t.strip()


def extract_json(text: str):
    t = strip_fences(text)
    starts = [i for i in (t.find("["), t.find("{")) if i != -1]
    start = min(starts) if starts else 0
    end = max(t.rfind("]"), t.rfind("}"))
    slice_ = t[start:end + 1] if (starts and end != -1) else t
    slice_ = slice_.translate(str.maketrans("\n\t\r", "   "))
    return json.loads(slice_)


def extract_sql(text: str) -> str:
    return strip_fences(text)


def call_claude_json_with_retry(client, trace, node, system, user_text, max_tokens, max_retries=2):
    """Same self-correction pattern as the SQL generator: if the response isn't
    valid JSON, send the parse error back to Claude and ask for a fix."""
    last_raw, last_err = None, None
    for attempt in range(max_retries + 1):
        if attempt == 0:
            msg = user_text
        else:
            msg = (
                f"{user_text}\n\nYour previous response could not be parsed as JSON. "
                f'Error: "{last_err}". Previous response:\n{last_raw}\n\n'
                f"Return corrected valid JSON only — escape any quotation marks "
                f"that appear inside string values."
            )
        try:
            raw = call_claude(client, system, msg, max_tokens)
        except Exception as e:
            trace.log(node, {}, msg, None, None, "error", str(e), attempt)
            raise
        try:
            parsed = extract_json(raw)
            trace.log(node, {}, msg, raw, parsed, "success", None, attempt)
            return parsed
        except Exception as e:
            last_raw, last_err = raw, str(e)
            trace.log(node, {}, msg, raw, None, "error", str(e), attempt)
            if attempt == max_retries:
                raise RuntimeError(f"Could not parse JSON after retries: {e}")


def run_sql(conn: sqlite3.Connection, sql: str):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    return cols, rows


def generate_and_run_sql(client, conn, trace, subq, max_retries=2):
    """Write SQL for a sub-question, run it, and self-correct on error —
    exactly the retry loop we validated in the artifact."""
    sql_history = []
    last_error = None
    for attempt in range(max_retries + 1):
        if attempt == 0:
            user_msg = (
                f'Sub-question: "{subq["question"]}"\n\n'
                f"Write the SQLite query following the rules and schema above."
            )
        else:
            user_msg = (
                f'Sub-question: "{subq["question"]}"\n\n'
                f"Your previous query:\n{sql_history[-1]}\n\n"
                f'It failed with this SQLite error: "{last_error}"\n\n'
                f"Return a corrected query. Only the SQL."
            )
        try:
            raw = call_claude(client, SYSTEM_SQLGEN, user_msg, max_tokens=900)
        except Exception as e:
            trace.log("sql_generator", {"sub_question": subq["question"]}, user_msg,
                       None, None, "error", str(e), attempt)
            return sql_history, None, f"Claude API call failed: {e}"

        sql = extract_sql(raw)
        sql_history.append(sql)
        trace.log("sql_generator", {"sub_question": subq["question"]}, user_msg,
                   raw, sql, "success", None, attempt)

        try:
            cols, rows = run_sql(conn, sql)
            trace.log("sql_executor", {"sql": sql}, None, None,
                       {"row_count": len(rows)}, "success", None, attempt)
            return sql_history, {"columns": cols, "rows": rows}, None
        except Exception as e:
            last_error = str(e)
            trace.log("sql_executor", {"sql": sql}, None, None, None,
                       "error", last_error, attempt)
            if attempt == max_retries:
                return sql_history, None, last_error


def format_result_for_prompt(subq, exec_result, exec_error):
    lines = [f"Sub-question: {subq['question']}"]
    if exec_error:
        lines.append(f"Result: ERROR — {exec_error}")
    else:
        lines.append(f"Columns: {', '.join(exec_result['columns'])}")
        lines.append("Rows:")
        for row in exec_result["rows"][:10]:
            lines.append("  " + " | ".join(str(v) for v in row))
        if not exec_result["rows"]:
            lines.append("  (no rows)")
    return "\n".join(lines)


# ---------- pipeline nodes (called from the LangGraph graph) ----------

def planner_node(state: dict, client: Anthropic) -> dict:
    trace = state["trace"]
    try:
        subquestions = call_claude_json_with_retry(
            client, trace, "planner", SYSTEM_PLANNER, state["seed_question"], max_tokens=700
        )
        return {**state, "subquestions": subquestions, "error": None}
    except Exception as e:
        return {**state, "error": f"Planning failed: {e}"}


def sql_stage_node(state: dict, client: Anthropic, conn: sqlite3.Connection) -> dict:
    trace = state["trace"]
    sub_results = []
    results_for_prompt = []
    for subq in state["subquestions"]:
        sql_history, exec_result, exec_error = generate_and_run_sql(client, conn, trace, subq)
        sub_results.append({
            "sub_question": subq["question"],
            "category": subq.get("category"),
            "sql_history": sql_history,
            "exec_result": exec_result,
            "exec_error": exec_error,
        })
        results_for_prompt.append(format_result_for_prompt(subq, exec_result, exec_error))
    return {**state, "sub_results": sub_results, "results_for_prompt": results_for_prompt, "error": None}


def synthesizer_node(state: dict, client: Anthropic) -> dict:
    trace = state["trace"]
    try:
        user_msg = f"Seed question: {state['seed_question']}\n\n" + "\n".join(state["results_for_prompt"])
        findings = call_claude_json_with_retry(
            client, trace, "synthesizer", SYSTEM_SYNTH, user_msg, max_tokens=2000
        )
        return {**state, "findings": findings, "error": None}
    except Exception as e:
        return {**state, "error": f"Synthesis failed: {e}"}


def strategist_node(state: dict, client: Anthropic) -> dict:
    trace = state["trace"]
    try:
        user_msg = (
            f"Seed question: {state['seed_question']}\n\n"
            f"Findings:\n{json.dumps(state['findings'], indent=2)}"
        )
        memo = call_claude_json_with_retry(
            client, trace, "strategist", SYSTEM_STRATEGIST, user_msg, max_tokens=2000
        )
        return {**state, "memo": memo, "error": None}
    except Exception as e:
        return {**state, "error": f"Memo drafting failed: {e}"}
