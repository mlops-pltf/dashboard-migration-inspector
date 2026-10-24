"""Tools: get_join_graph, compare_joins, analyze_row_multiplication."""

from __future__ import annotations

import functools

from strands import tool

from ..lineage_tracer.resolve import home_table
from ..lineage_tracer.tool import resolved
from ..package_inventory.loader import load_cached, location_of, package_key
from .compare import analyze_containers, compare_declared
from .graph import Edge, KeyCatalog, model_graph


@functools.lru_cache(maxsize=32)
def _graphs(location: str, key: str):
    pkg = load_cached(location)
    ts, st = model_graph(pkg.thoughtspot.models[0]), model_graph(pkg.strategy.models[0])
    catalog = KeyCatalog()
    catalog.absorb(ts)
    catalog.absorb(st)
    return ts, st, catalog


def graphs(package: str):
    location = location_of(package)
    return _graphs(location, package_key(location))


def edge_dict(e: Edge | None) -> dict | None:
    if e is None:
        return None
    return {"name": e.name, "from": str(e.left), "to": str(e.right), "type": e.type,
            "keys": [f"{a} = {b}" for a, b in e.keys], "cardinality": e.cardinality, "origin": e.origin,
            "applied": "always (inside one SQL)" if e.always else "only when the query needs it",
            **({"inside": e.container} if e.container else {}),
            "source": str(e.source) if e.source else None}


def get_join_graph_impl(package: str, side: str) -> dict:
    ts, st, catalog = graphs(package)
    g = ts if side == "thoughtspot" else st
    return {"side": side, "joins": [edge_dict(e) for e in g.edges],
            "known_unique_keys": sorted(", ".join(map(str, sorted(k, key=str))) for k in catalog.unique),
            "known_non_unique_keys": sorted(", ".join(map(str, sorted(k, key=str))) for k in catalog.non_unique)}


def compare_joins_impl(package: str) -> dict:
    ts, st, _ = graphs(package)
    return {"comparisons": [{"thoughtspot": edge_dict(c.thoughtspot), "strategy": edge_dict(c.strategy),
                             "issues": [{"check": k, "explanation": v} for k, v in c.issues] or "none"}
                            for c in compare_declared(ts, st)]}


def analyze_row_multiplication_impl(package: str) -> dict:
    _, st, catalog = graphs(package)
    measures = {name: r for name, r in resolved(package, "strategy").items() if r.kind == "MEASURE"}
    homes: dict = {}
    for name, r in measures.items():
        h = home_table(r)
        if h is not None:
            homes.setdefault(h, []).append(name)
    results = analyze_containers(st, catalog, set(homes))
    if not results:
        return {"result": "no Freeform SQL table joins measure tables; nothing can multiply rows here"}
    return {"tables": [{
        "sql_table": a.container, "measure_home": str(a.home), "measures": homes.get(a.home, []),
        "verdict": a.verdict,
        **({"other_fact_tables_joined": a.other_fact_tables} if a.other_fact_tables else {}),
        "joins": [{"join": m.edge.name, "verdict": m.verdict, "evidence": m.evidence} for m in a.joins],
    } for a in results],
        "note": "MIN, MAX and COUNT DISTINCT are not affected by repeated rows; SUM, COUNT, AVG are."}


@tool
def get_join_graph(package: str, side: str) -> dict:
    """The join graph of one side over warehouse tables. ThoughtSpot: declared joins (type, keys, cardinality,
    applied only when needed unless progressive joins are off). Strategy: joins derived from attributes that
    live on several tables (type from the model's join-semantics properties) plus joins written inside
    Freeform SQL (always applied). Also lists which key columns the metadata says are unique / not unique.

    Args:
        package: Path of the package folder or zip.
        side: "thoughtspot" or "strategy".
    """
    return get_join_graph_impl(package, side)


@tool
def compare_joins(package: str) -> dict:
    """Compare every declared ThoughtSpot join with its Strategy counterpart: does a path exist, same keys,
    same join type, and same applicability (an INNER join applied to every query inside a Freeform SQL drops
    rows even when the joined table is not used). Issues carry suggested check ids (JN-01/02/06/07).

    Args:
        package: Path of the package folder or zip.
    """
    return compare_joins_impl(package)


@tool
def analyze_row_multiplication(package: str) -> dict:
    """For each Freeform SQL table in the Strategy model and each fact table whose measures it carries: does any
    join repeat the fact's rows (fan-out), join another fact through a shared dimension (chasm trap), or is
    uniqueness unknown from the metadata (then a data key profile decides)?

    Args:
        package: Path of the package folder or zip.
    """
    return analyze_row_multiplication_impl(package)
