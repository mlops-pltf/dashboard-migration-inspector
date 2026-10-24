"""Compare join graphs between the tools, and find row multiplication inside Freeform SQL tables."""

from __future__ import annotations

from dataclasses import dataclass, field

from inspector_contract import PhysicalTable
from .graph import Edge, JoinGraph, KeyCatalog, Multiplication, row_multiplication


@dataclass
class EdgeComparison:
    thoughtspot: Edge
    strategy: Edge | None  # oriented like the ThoughtSpot edge
    issues: list[tuple[str, str]] = field(default_factory=list)  # (check id, explanation)

    @property
    def ok(self) -> bool:
        return not self.issues


def compare_declared(ts: JoinGraph, st: JoinGraph) -> list[EdgeComparison]:
    """Every declared ThoughtSpot join vs its Strategy counterpart: path exists, keys, type, applicability."""
    out = []
    for e in ts.edges:
        if e.origin != "declared":
            continue
        candidates = st.between(e.left, e.right)
        if not candidates:
            out.append(EdgeComparison(e, None, [("JN-07", f"no join between {e.left} and {e.right} in Strategy")]))
            continue
        se = candidates[0].oriented(e.left)
        c = EdgeComparison(e, se)
        if se.key_set() != e.key_set():
            c.issues.append(("JN-02", "different join keys"))
        if se.type == "UNKNOWN":
            c.issues.append(("JN-01?", "the Strategy input does not carry the join type"))
        elif se.type != e.type:
            effect = "rows without a match are dropped" if se.type == "INNER" else "rows without a match are kept"
            c.issues.append(("JN-01", f"{e.type} in ThoughtSpot, {se.type} in Strategy: {effect}"))
        elif e.type == "INNER" and not e.always and se.always:
            c.issues.append(("JN-06", f"INNER join applied to every query in Strategy but only when {e.right} is "
                                      f"used in ThoughtSpot: unmatched {e.left} rows vanish everywhere"))
        out.append(c)
    return out


@dataclass
class HomeAnalysis:
    container: str
    home: PhysicalTable
    verdict: str  # safe | fan-out | chasm | unknown
    joins: list[Multiplication]
    other_fact_tables: list[str]


def analyze_containers(st: JoinGraph, catalog: KeyCatalog, homes: set[PhysicalTable]) -> list[HomeAnalysis]:
    """For each Freeform SQL table and each measure home inside it: are the home's rows multiplied?"""
    out = []
    for container in sorted({e.container for e in st.edges if e.origin == "sql" and e.container}):
        edges = [e for e in st.edges if e.container == container]
        tables = {t for e in edges for t in (e.left, e.right)}
        inside = {h for h in homes if h in tables}
        for home in sorted(inside, key=str):
            issues = [x for x in row_multiplication(edges, home, catalog) if x.verdict != "safe"]
            proven = [x for x in issues if x.verdict == "multiplies"]
            others = inside - {home}
            chasm = [x for x in proven if x.far_side & others]
            verdict = "chasm" if chasm else "fan-out" if proven else "unknown" if issues else "safe"
            out.append(HomeAnalysis(container, home, verdict, proven or issues,
                                    sorted(t.table for x in chasm for t in x.far_side & others)))
    return out
