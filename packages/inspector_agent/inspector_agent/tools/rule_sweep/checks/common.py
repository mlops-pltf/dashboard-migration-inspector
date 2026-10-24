from __future__ import annotations

from inspector_contract import Agg, Expr, to_text, walk
from ...findings_ledger.report import ObjectRef

from ..context import Context

ADDITIVE = {"SUM", "AVG", "STDDEV", "VARIANCE", "MEDIAN"}


def ts_ref(ctx: Context, name: str) -> ObjectRef:
    f = ctx.ts.field(name)
    r = ctx.ts_res.get(name)
    return ObjectRef(model=ctx.ts.name, name=name, kind="field",
                     location=str(f.source) if f and f.source else None,
                     definition=to_text(r.expr) if r else None)


def st_ref(ctx: Context, name: str) -> ObjectRef:
    f = ctx.st.field(name)
    r = ctx.st_res.get(name)
    return ObjectRef(model=ctx.st.name, name=name, kind="field",
                     location=str(f.source) if f and f.source else None,
                     definition=to_text(r.expr) if r else None)


def fanout_sensitive(e: Expr) -> bool:
    """True if repeated rows change the value (SUM, AVG, plain COUNT...). MIN, MAX, COUNT DISTINCT are safe."""
    return any(isinstance(n, Agg) and (n.fn in ADDITIVE or (n.fn == "COUNT" and not n.distinct)) for n in walk(e))
