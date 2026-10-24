"""Joins: JN-01 type, JN-02 keys, JN-06 applicability, JN-07 missing path, JN-04 fan-out, JN-05 chasm.
The analysis itself lives in join_analyzer; this module turns it into leads."""

from __future__ import annotations

from collections import defaultdict

from ...findings_ledger.report import Finding, ObjectRef
from ...join_analyzer.compare import analyze_containers, compare_declared
from ...join_analyzer.graph import Edge
from ..context import Context
from .common import fanout_sensitive, ts_ref

_SUGGEST = {
    "JN-01": "Use the ThoughtSpot join type in Strategy.",
    "JN-02": "Join on the same key columns as ThoughtSpot.",
    "JN-06": "Move the table out of the Freeform SQL into its own table, or LEFT JOIN it.",
    "JN-07": "Relate the tables in Strategy on the ThoughtSpot join keys.",
}


def _edge_ref(model: str, e: Edge | None) -> ObjectRef | None:
    if e is None:
        return None
    keys = " AND ".join(f"{a} = {b}" for a, b in e.keys)
    how = "always applied" if e.always else "applied when needed"
    return ObjectRef(model=model, name=e.name, kind="join", location=str(e.source) if e.source else None,
                     definition=f"{e.left} {e.type} JOIN {e.right} ON {keys} ({how})")


def _orphan_probe(e: Edge, se: Edge | None) -> dict:
    return {"type": "orphan_profile", "join": e.name, "left": str(e.left), "right": str(e.right),
            "left_keys": [a.column for a, _ in e.keys], "right_keys": [b.column for _, b in e.keys],
            "thoughtspot_type": e.type, "strategy_type": se.type if se else None, "cardinality": e.cardinality}


def _kpi_probe(ts: str, st: str) -> dict:
    return {"type": "kpi_equivalence", "thoughtspot": ts, "strategy": st}


def run(ctx: Context) -> list[Finding]:
    out = []
    ts_homes, st_homes = defaultdict(list), defaultdict(list)
    for m in ctx.pairs:
        if ctx.ts_res[m.ts].kind == "MEASURE":
            ts_homes[ctx.home(ctx.ts_res[m.ts])].append(m)
            st_homes[ctx.home(ctx.st_res[m.st])].append(m)

    for c in compare_declared(ctx.ts_graph, ctx.st_graph):
        e = c.thoughtspot
        refs = dict(thoughtspot=_edge_ref(ctx.ts.name, e), strategy=_edge_ref(ctx.st.name, c.strategy),
                    symptoms=[m.ts for m in ts_homes.get(e.left, [])])
        if c.ok:
            out.append(Finding(check="JN-01", severity="BLOCKER", status="PASS",
                               title=f"{e.left} -> {e.right}: same join type and keys", **refs))
        for check, why in c.issues:
            unknown = check.endswith("?")
            out.append(Finding(check=check.rstrip("?"), severity="BLOCKER", status="UNVERIFIED" if unknown else "FAIL",
                               title=f"{e.left} -> {e.right}: {why}",
                               needs=["data:orphan_profile", "data:kpi_equivalence"] if unknown else ["data:orphan_profile"],
                               probes=[_orphan_probe(e, c.strategy)] if check.rstrip("?") in ("JN-01", "JN-06") else [],
                               suggested_fix=None if unknown else _SUGGEST.get(check), **refs))

    for a in analyze_containers(ctx.st_graph, ctx.catalog, set(st_homes)):
        matches = st_homes[a.home]
        affected = [m for m in matches if fanout_sensitive(ctx.st_res[m.st].expr)]
        if a.verdict == "safe" or not affected:
            continue
        root, rest = affected[0], [m.ts for m in affected[1:]]
        table = ctx.st.table(a.container)
        container_ref = ObjectRef(model=ctx.st.name, name=a.container, kind="relation",
                                  location=str(table.source) if table and table.source else None)
        joins = "; ".join(f"{x.edge.name} ({x.verdict}: {x.evidence})" for x in a.joins)
        common = dict(thoughtspot=ts_ref(ctx, root.ts), strategy=container_ref, symptoms=rest,
                      evidence={"joins": joins})
        impact = [_kpi_probe(root.ts, root.st)]
        if a.verdict == "chasm":
            out.append(Finding(check="JN-05", severity="BLOCKER", status="FAIL",
                               title=f"Chasm trap: {a.home.table} measures are joined to "
                                     f"{', '.join(a.other_fact_tables)} in one SQL",
                               needs=["data:impact"], probes=impact,
                               suggested_fix="Keep each fact in its own table, or pre-aggregate each fact to the "
                                             "shared grain before joining.", **common))
        elif a.verdict == "fan-out":
            out.append(Finding(check="JN-04", severity="BLOCKER", status="FAIL",
                               title=f"Fan-out: {a.home.table} rows are repeated by a one-to-many join",
                               needs=["data:impact"], probes=impact,
                               suggested_fix="Aggregate the many side before joining, or move it to its own table.",
                               **common))
        else:
            out.append(Finding(check="JN-04", severity="BLOCKER", status="UNVERIFIED",
                               title=f"Possible fan-out: {a.home.table} is joined on columns not known to be unique",
                               needs=["data:key_profile"],
                               probes=[{"type": "key_profile", "source": str(x.far_keys[0].physical_table),
                                        "keys": [k.column for k in x.far_keys], "join": x.edge.name}
                                       for x in a.joins if x.verdict == "unknown" and x.far_keys], **common))
    return out
