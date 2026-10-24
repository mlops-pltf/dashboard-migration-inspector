"""Tool: analyze_sql."""

from __future__ import annotations

from strands import tool

from inspector_contract import to_text
from .lineage import analyze


def analyze_sql_impl(sql: str, default_schema: str = "public") -> dict:
    lin = analyze(sql, default_namespace=default_schema)
    return {
        "outputs": {k: to_text(v) for k, v in lin.outputs.items()},
        "sources": {alias: str(t) for alias, t in lin.sources.items()},
        "root": lin.root,
        "joins": [{"type": j.type, "join": j.right, "after": list(j.left),
                   "keys": [f"{a} = {b}" for a, b in j.keys],
                   **({"extra_condition": to_text(j.extra)} if j.extra is not None else {})} for j in lin.joins],
        "where": [to_text(w) for w in lin.where],
        "group_by": [to_text(g) for g in lin.group_by],
        "distinct": lin.distinct,
        "errors": list(lin.errors),
    }


@tool
def analyze_sql(sql: str, default_schema: str = "public") -> dict:
    """Analyse a SELECT (Freeform SQL, SQL view, generated SQL): for every output column the warehouse columns it
    comes from, every join (type, keys, extra conditions), WHERE predicates, GROUP BY and DISTINCT. CTEs and
    subqueries are flattened. Redshift dialect.

    Args:
        sql: The SQL text.
        default_schema: Schema for unqualified table names.
    """
    return analyze_sql_impl(sql, default_schema)
