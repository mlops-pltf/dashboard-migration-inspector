"""data_agent: the team's only access to the warehouse, an A2A server (port 9000) on Bedrock AgentCore.

Structured skills (answered by code, never by the LLM):
  run_query       {sql, max_rows?}                               -> rows
  describe_table  {table}                                        -> columns, row count
  key_profile     {source, keys}                                 -> uniqueness of a key
  orphan_profile  {left, right, left_keys, right_keys?}          -> rows without a partner across a join
  column_profile  {source, column, top?}                         -> nulls, distinct, frequent values
  compare_queries {left_sql, right_sql, keys, measures, abs_tolerance?, rel_tolerance?} -> reconciliation
  check_sql       {sql}                                          -> allowed?, warehouse SQL
Conversation: any text message, answered by the Strands agent with its tools.
"""

from __future__ import annotations

import os
from pathlib import Path

from strands import Agent
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from inspector_contract.a2a import SkillRouterExecutor, agent_card

from .llm import get_model, warm_up
from .tools.profiler import profile
from .tools.profiler.tool import column_profile, key_profile, orphan_profile
from .tools.reconciler.reconcile import compare_queries as compare_queries_impl
from .tools.reconciler.tool import compare_queries
from .tools.sql_guard.tool import check_sql, check_sql_impl
from .tools.warehouse.tool import describe_table, describe_table_impl, run_query, run_query_impl

NAME = "data_agent"
DESCRIPTION = "Data agent: read-only warehouse access for the team: queries, key and join profiles, reconciliation."
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()
TOOLS = [describe_table, run_query, key_profile, orphan_profile, column_profile, compare_queries, check_sql]

SKILLS = {
    "run_query": lambda a: run_query_impl(a["sql"], a.get("max_rows", 200)),
    "describe_table": lambda a: describe_table_impl(a["table"]),
    "key_profile": lambda a: profile.key_profile(a["source"], a["keys"]),
    "orphan_profile": lambda a: profile.orphan_profile(a["left"], a["right"], a["left_keys"], a.get("right_keys")),
    "column_profile": lambda a: profile.column_profile(a["source"], a["column"], a.get("top", 10)),
    "compare_queries": lambda a: compare_queries_impl(a["left_sql"], a["right_sql"], a.get("keys"), a["measures"],
                                                      a.get("abs_tolerance", 0.01), a.get("rel_tolerance", 0.0)),
    "check_sql": lambda a: check_sql_impl(a["sql"]),
}
SKILL_DOCS = {
    "run_query": "One read-only SELECT (Redshift dialect) -> columns, rows (capped).",
    "describe_table": "Columns, types and row count of schema.table.",
    "key_profile": "Uniqueness of key columns in a table or SELECT, with duplicate samples.",
    "orphan_profile": "Rows on each side of a join with no partner (what INNER drops).",
    "column_profile": "Nulls, distinct count, min/max and frequent values of a column.",
    "compare_queries": "Reconcile two result sets on key columns: one-sided rows, measure differences, totals.",
    "check_sql": "Whether a query is allowed, and its warehouse-dialect form. Does not run it.",
}


def build_agent(model=None) -> Agent:
    return Agent(model=model or get_model("fast"), tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                 callback_handler=None, name=NAME)


def executor(conversation: bool = True) -> SkillRouterExecutor:
    chat = StrandsA2AExecutor(agent_factory=lambda _context_id: build_agent()) if conversation else None
    return SkillRouterExecutor(SKILLS, chat)


def card(url: str):
    return agent_card(NAME, DESCRIPTION, url, SKILL_DOCS,
                      conversation="Ask a question the warehouse can answer: counts, keys, joins, differences.")


def app(url: str = "http://localhost:9000/", conversation: bool = True):
    """The A2A ASGI app (used by tests and by `main`)."""
    from bedrock_agentcore.runtime import build_a2a_app

    return build_a2a_app(executor(conversation), card(url), runtime_url=url)


def main() -> None:
    from bedrock_agentcore.runtime import serve_a2a

    from inspector_contract import telemetry

    telemetry.setup(NAME)
    warm_up()
    serve_a2a(executor(), card(os.environ.get("AGENTCORE_RUNTIME_URL", "http://localhost:9000/")))


if __name__ == "__main__":
    main()
