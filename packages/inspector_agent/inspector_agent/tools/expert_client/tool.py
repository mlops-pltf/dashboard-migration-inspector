"""Tools: ask_expert, analyze_sql."""

from __future__ import annotations

from strands import tool

from .client import expert


def ask_expert_impl(side: str, question: str, context_id: str | None = None) -> dict:
    answer, context = expert(side).ask(question, context_id)
    return {"expert": side, "answer": answer, "context_id": context,
            "note": "An expert's answer explains its tool; it is evidence of meaning, not a proof of equality."}


def analyze_sql_impl(side: str, sql: str, default_schema: str = "public") -> dict:
    return expert(side).call("analyze_sql", {"sql": sql, "default_schema": default_schema})


@tool
def ask_expert(side: str, question: str, context_id: str | None = None) -> dict:
    """Ask the ThoughtSpot or Strategy expert agent a question about its side of the package, e.g. what a
    formula really computes, how a join is applied, what a setting means, what the input format lost. Include
    the package location in the question. Pass the returned context_id to continue the same conversation.

    Args:
        side: "thoughtspot" or "strategy".
        question: The question, with the package location and object names.
        context_id: Conversation to continue, if any.
    """
    return ask_expert_impl(side, question, context_id)


@tool
def analyze_sql(side: str, sql: str, default_schema: str = "public") -> dict:
    """Have an expert analyse a SELECT (its dialect knowledge applies): lineage of every output column, joins,
    WHERE, GROUP BY. Use for SQL that is not part of the model, e.g. supplied generated SQL.

    Args:
        side: "thoughtspot" or "strategy" (whose expert should read it).
        sql: The SQL text.
        default_schema: Schema for unqualified tables.
    """
    return analyze_sql_impl(side, sql, default_schema)
