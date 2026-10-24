"""FL-01: model-level filters (logic in filter_inspector)."""

from __future__ import annotations

from ...filter_inspector.filters import compare
from ...findings_ledger.report import Finding, ObjectRef
from ..context import Context


def _ref(model: str, info) -> ObjectRef:
    return ObjectRef(model=model, name=info.origin, kind="filter", location=info.source, definition=info.condition)


def run(ctx: Context) -> list[Finding]:
    measures = [m.ts for m in ctx.pairs if ctx.ts_res[m.ts].kind == "MEASURE"]
    c = compare(ctx.ts, ctx.st)
    out = []
    for x in c["matched"]:
        out.append(Finding(check="FL-01", severity="BLOCKER", status="PASS", title=f"Filter present: {x.condition}",
                           thoughtspot=_ref(ctx.ts.name, x),
                           strategy=_ref(ctx.st.name, c["strategy_by_condition"][x.condition])))
    for x in c["missing_in_strategy"]:
        out.append(Finding(check="FL-01", severity="BLOCKER", status="FAIL",
                           title=f"ThoughtSpot filter missing in Strategy: {x.condition}",
                           thoughtspot=_ref(ctx.ts.name, x), symptoms=measures, needs=["data:impact"],
                           suggested_fix=f"Add `{x.condition}` to the Strategy model (e.g. the Freeform SQL WHERE clause)."))
    for x in c["only_in_strategy"]:
        out.append(Finding(check="FL-01", severity="BLOCKER", status="FAIL",
                           title=f"Strategy applies a filter ThoughtSpot does not: {x.condition}",
                           strategy=_ref(ctx.st.name, x), symptoms=measures, needs=["data:impact"],
                           suggested_fix="Remove the filter, or confirm it was intended and record it in mapping.yaml."))
    return out
