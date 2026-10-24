"""Tools: run_query, describe_table."""

from __future__ import annotations

from strands import tool

from ..sql_guard.guard import allowed_schemas, guard
from .connection import fetch, identifier

MAX_ROWS = 1000


def run_query_impl(sql: str, max_rows: int = 200) -> dict:
    max_rows = max(1, min(int(max_rows), MAX_ROWS))
    g = guard(sql, max_rows=max_rows)
    if not g.ok:
        return {"error": "query rejected", "reasons": g.reasons}
    columns, rows = fetch(g.sql)
    return {"columns": columns, "rows": rows[:max_rows], "row_count": min(len(rows), max_rows),
            "truncated": len(rows) > max_rows, "tables": g.tables, "executed_sql": g.sql}


def describe_table_impl(table: str) -> dict:
    name = identifier(table)
    if "." not in name:
        return {"error": "table must be schema-qualified"}
    schema, tbl = name.split(".")
    if schema not in allowed_schemas():
        return {"error": f"schema {schema} is not allowed"}
    _, cols = fetch("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (schema, tbl))
    if not cols:
        return {"error": f"table {name} not found"}
    _, count = fetch(f"SELECT COUNT(*) FROM {name}")
    return {"table": name, "row_count": count[0][0],
            "columns": [{"name": c, "type": t, "nullable": n == "YES"} for c, t, n in cols]}


@tool
def run_query(sql: str, max_rows: int = 200) -> dict:
    """Run one read-only SELECT on the warehouse and return its rows (at most max_rows, flagged when truncated).

    Args:
        sql: The query, Redshift dialect, schema-qualified tables.
        max_rows: Row cap (at most 1000). Aggregate in SQL instead of fetching detail rows.
    """
    return run_query_impl(sql, max_rows)


@tool
def describe_table(table: str) -> dict:
    """Columns, types, nullability and row count of a warehouse table.

    Args:
        table: schema.table
    """
    return describe_table_impl(table)
