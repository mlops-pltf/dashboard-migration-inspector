"""Tool: compare_queries."""

from __future__ import annotations

from strands import tool

from .reconcile import compare_queries as compare_queries_impl


@tool
def compare_queries(left_sql: str, right_sql: str, keys: list[str] | None, measures: list,
                    abs_tolerance: float = 0.01, rel_tolerance: float = 0.0) -> dict:
    """Run two queries and reconcile their results inside the warehouse: rows present on one side only, rows
    whose measures differ beyond tolerance (with the largest differences as samples), and totals per measure.

    Args:
        left_sql: First query (e.g. the ThoughtSpot side), returning the key columns and the measures.
        right_sql: Second query (e.g. the Strategy side).
        keys: Key (dimension) columns present in both results; empty for a single grand-total row.
        measures: Measure columns, each a name present in both, or [left_name, right_name].
        abs_tolerance: Allowed absolute difference (default 0.01).
        rel_tolerance: Allowed relative difference, e.g. 0.0001.
    """
    return compare_queries_impl(left_sql, right_sql, keys, measures, abs_tolerance, rel_tolerance)
