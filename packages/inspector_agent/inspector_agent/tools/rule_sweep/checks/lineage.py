"""LN-01: matched attributes read the same physical column (join-key attributes may use either side)."""

from __future__ import annotations

from inspector_contract import PCol
from ...findings_ledger.report import Finding
from ...definition_comparer.canonical import same

from ..context import Context
from .common import st_ref, ts_ref


def run(ctx: Context) -> list[Finding]:
    out = []
    for m in ctx.pairs:
        t, s = ctx.ts_res[m.ts], ctx.st_res[m.st]
        if t.kind != "ATTRIBUTE" or s.kind != "ATTRIBUTE":
            continue
        common = dict(thoughtspot=ts_ref(ctx, m.ts), strategy=st_ref(ctx, m.st), match_basis=m.basis)
        if t.status != "exact" or s.status != "exact":
            out.append(Finding(check="LN-01", severity="BLOCKER", status="UNVERIFIED",
                               title=f"{m.ts}: lineage could not be fully resolved",
                               evidence={"issues": "; ".join(t.issues + s.issues)}, needs=["data:dimension_values"],
                               **common))
            continue
        if isinstance(t.expr, PCol) and isinstance(s.expr, PCol):
            options = set(s.equivalent_columns or s.columns)
            ok = t.expr.column in options or bool(set(t.equivalent_columns) & options)
        else:
            ok = same(t.expr, s.expr)
        if ok:
            out.append(Finding(check="LN-01", severity="BLOCKER", status="PASS",
                               title=f"{m.ts} reads the same column", **common))
        else:
            out.append(Finding(
                check="LN-01", severity="BLOCKER", status="FAIL",
                title=f"{m.ts} reads a different column in Strategy",
                evidence={"ThoughtSpot column": ", ".join(map(str, t.columns)),
                          "Strategy column(s)": ", ".join(map(str, s.equivalent_columns or s.columns))},
                suggested_fix=f"Point the Strategy attribute {m.st} at {', '.join(map(str, t.columns))}.",
                **common))
    return out
