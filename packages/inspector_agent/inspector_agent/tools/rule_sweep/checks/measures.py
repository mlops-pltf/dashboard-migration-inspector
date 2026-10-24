"""Measure definitions: proof by canonical form, otherwise a classified difference (see definition_comparer)."""

from __future__ import annotations

from inspector_contract import to_text
from ...definition_comparer.canonical import canonical
from ...definition_comparer.classify import classify
from ...findings_ledger.report import Finding

from ..context import Context
from .common import st_ref, ts_ref


def run(ctx: Context) -> list[Finding]:
    out = []
    for m in ctx.pairs:
        t, s = ctx.ts_res[m.ts], ctx.st_res[m.st]
        if t.kind != "MEASURE" and s.kind != "MEASURE":
            continue
        common = dict(thoughtspot=ts_ref(ctx, m.ts), strategy=st_ref(ctx, m.st), match_basis=m.basis)
        if t.kind != s.kind:
            out.append(Finding(check="FX-01", severity="BLOCKER", status="FAIL",
                               title=f"{m.ts} is a {t.kind.lower()} in ThoughtSpot but a {s.kind.lower()} in Strategy",
                               **common))
            continue
        ct, cs = canonical(t.expr), canonical(s.expr)
        if ct == cs:
            out.append(Finding(check="FX-01", severity="BLOCKER", status="PASS", title=f"{m.ts}: identical definition",
                               evidence={"canonical": to_text(ct)}, **common))
            continue
        check, status, why = classify(ct, cs, ctx.st.lossy)
        needs = ["llm:formula_equivalence", "data:kpi_equivalence"] if status == "UNVERIFIED" else ["data:impact"]
        if check == "AG-02" and status == "UNVERIFIED":
            needs = ["data:kpi_equivalence"]
        out.append(Finding(check=check, severity="BLOCKER", status=status, title=f"{m.ts}: {why}",
                           decided_by="rule", needs=needs,
                           evidence={"ThoughtSpot (canonical)": to_text(ct), "Strategy (canonical)": to_text(cs)},
                           probes=[{"type": "kpi_equivalence", "thoughtspot": m.ts, "strategy": m.st}],
                           **common))
    return out
