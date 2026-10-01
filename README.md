# Readmission Exposure Analyst — Agentic BI

An AI agent that investigates CMS Hospital Readmissions Reduction Program (HRRP)
data like a healthcare strategy analyst: it plans its own sub-questions, writes
and runs its own SQL against a real SQLite database, self-corrects on errors,
synthesizes material findings, and drafts an executive memo — with every
reasoning step logged for inspection.

## Stack

Python · LangGraph · Claude API (Anthropic) · SQLite

## Setup

1. Create a virtual environment and install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and add your Anthropic API key:
   ```
   cp .env.example .env
   ```
3. Run it:
   ```
   python -m src.main "Which hospitals are most exposed to readmission penalties, and what's driving it?"
   ```
   Or with no argument, it uses a default seed question.

## What happens when you run it

1. **Planner** — breaks your question into 4-6 sub-questions covering scope,
   ranking, structural correlates, and contrast.
2. **SQL generator + executor** — writes SQL for each sub-question, runs it
   against `db/hospital_readmissions.db`, and self-corrects up to 2 times if
   the query errors.
3. **Synthesizer** — reviews all the results and picks out only the findings
   that are business-material, not just descriptive.
4. **Strategist** — drafts a short executive memo: situation, key findings,
   recommendation.

If any stage fails outright (not a retriable SQL/JSON error, but something
like an API failure), the graph stops and reports where, rather than pushing
broken data forward.

## Where things land

- `logs/run_<timestamp>.json` — the full reasoning trace: every prompt sent,
  every response, every retry, every SQL query and its result.
- `outputs/memo_<timestamp>.md` — the final strategist memo.

## Data

`hospitals` (5,432 rows) and `readmission_measures` (18,330 rows), built from
two CMS Provider Data Catalog files: Hospital General Information and the
Hospital Readmissions Reduction Program dataset, joined on `facility_id`.
Note: the original two CMS files stored `facility_id` differently (zero-padded
text vs. plain number) — this was found and fixed during cleaning; see the
project history for details.

## Design note on retries

SQL and JSON self-correction happens as an inner retry loop inside the
`sql_stage` and JSON-producing nodes, not as separate LangGraph edges. The
graph's edges model node-to-node routing (including a fail-fast path to `END`
if any stage errors out); retrying a sub-step a couple of times inside one
node's own logic is ordinary control flow and doesn't need its own edge.
