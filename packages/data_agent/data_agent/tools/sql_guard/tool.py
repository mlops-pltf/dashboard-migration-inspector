"""Tool: check_sql."""

from __future__ import annotations

from strands import tool

from .guard import guard


def check_sql_impl(sql: str) -> dict:
    g = guard(sql)
    return {"allowed": g.ok, "tables": g.tables, "reasons": g.reasons, "warehouse_sql": g.sql}


@tool
def check_sql(sql: str) -> dict:
    """Check whether a query may run on the warehouse (one read-only SELECT on allowed schemas, schema-qualified
    tables) and show it translated to the warehouse dialect. Does not run it.

    Args:
        sql: The query, Redshift dialect.
    """
    return check_sql_impl(sql)
