"""Tool: compare_filters."""

from __future__ import annotations

from dataclasses import asdict

from strands import tool

from ..counterpart_finder.matching import pick_models
from ..package_inventory.loader import load_cached
from .filters import compare


def compare_filters_impl(package: str) -> dict:
    ts, st = pick_models(load_cached(package))
    c = compare(ts, st)
    return {"matched": [asdict(x) for x in c["matched"]],
            "missing_in_strategy": [asdict(x) for x in c["missing_in_strategy"]],
            "only_in_strategy": [asdict(x) for x in c["only_in_strategy"]],
            "note": "Model filters apply to every query: a missing or extra one changes every measure."}


@tool
def compare_filters(package: str) -> dict:
    """Compare model-level filters: ThoughtSpot worksheet/model filters vs Strategy model filters and the WHERE
    clauses of SQL-backed tables. Conditions are compared in canonical form over warehouse columns, so
    `NOT IN ('x')` and `<> 'x'` match. Lists matched, missing-in-Strategy and Strategy-only filters.

    Args:
        package: Path of the package folder or zip.
    """
    return compare_filters_impl(package)
