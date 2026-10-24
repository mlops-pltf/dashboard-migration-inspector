"""Tool: compile_query."""

from __future__ import annotations

from strands import tool

from inspector_contract import FilterSpec
from ..counterpart_finder.matching import pick_models
from ..package_inventory.loader import load_cached
from .compile import compile_query


def compile_query_impl(package: str, side: str, measures: list[str], dimensions: list[str] | None = None,
                       filters: list[dict] | None = None) -> dict:
    if side not in ("thoughtspot", "strategy"):
        return {"error": "side must be thoughtspot or strategy"}
    ts, st = pick_models(load_cached(package))
    specs = [FilterSpec(field=f["field"], op=f.get("op", "in"), values=tuple(str(v) for v in f.get("values", [])))
             for f in filters or []]
    return compile_query(ts if side == "thoughtspot" else st, measures, dimensions or [], specs).as_dict()


@tool
def compile_query(package: str, side: str, measures: list[str], dimensions: list[str] | None = None,
                  filters: list[dict] | None = None) -> dict:
    """Show the SQL one side's model computes for measures by dimensions (what a data probe would run): joins
    chosen from each measure's home table, model filters, levels, multi-fact merging. Lists the assumptions
    made and anything that cannot be compiled from the export.

    Args:
        package: Path of the package folder or zip.
        side: thoughtspot or strategy.
        measures: Measure names on that side.
        dimensions: Attribute names on that side to group by (none = grand total).
        filters: Optional [{"field": ..., "op": "in", "values": [...]}].
    """
    return compile_query_impl(package, side, measures, dimensions, filters)
