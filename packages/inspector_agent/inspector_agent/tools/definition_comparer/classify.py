"""Classify why two canonical metric definitions differ.

Order: unparsed -> AG-01 aggregate function -> AG-02 level -> FL-02 condition -> AG-03 aggregation order ->
FX-03 division by zero -> FX-01 (not provable by rules).
"""

from __future__ import annotations

from inspector_contract import Agg, BinOp, Case, Expr, Lit, Opaque, children, columns, transform, walk

_NONLINEAR = ("/", "*")


def _aggs(e: Expr) -> list[Agg]:
    return [n for n in walk(e) if isinstance(n, Agg)]


def _nonlinear(e: Expr, inside: bool = False) -> tuple[bool, bool]:
    """(nonlinear op inside an aggregate, nonlinear op above aggregates)."""
    row = post = False
    if isinstance(e, Agg):
        for c in (e.arg, e.condition):
            if c is not None:
                r, _ = _nonlinear(c, True)
                row = row or r
        return row, post
    if isinstance(e, BinOp) and e.op in _NONLINEAR:
        if inside:
            row = True
        else:
            post = True
    for c in children(e):
        r, p = _nonlinear(c, inside)
        row, post = row or r, post or p
    return row, post


def _strip_safe_divide(e: Expr) -> Expr:
    """CASE WHEN 0 = b THEN 0 ELSE a / b END  ->  a / b  (the only difference is the zero branch)."""
    def rule(n: Expr) -> Expr | None:
        if isinstance(n, Case) and len(n.whens) == 1 and isinstance(n.else_, BinOp) and n.else_.op == "/":
            cond, value = n.whens[0]
            if isinstance(value, Lit) and value.value == 0 and isinstance(cond, BinOp) and cond.op == "=" \
                    and n.else_.right in (cond.left, cond.right):
                return n.else_
        return None
    return transform(e, rule)


def classify(ts: Expr, st: Expr, st_lossy: tuple[str, ...]) -> tuple[str, str, str]:
    """-> (check, status, explanation) for two canonical expressions known to differ."""
    if any(isinstance(n, Opaque) for n in walk(ts)) or any(isinstance(n, Opaque) for n in walk(st)):
        reason = next(n.reason for n in walk(st) if isinstance(n, Opaque)) if any(
            isinstance(n, Opaque) for n in walk(st)) else next(n.reason for n in walk(ts) if isinstance(n, Opaque))
        return "FX-01", "UNVERIFIED", f"definition could not be read: {reason}"
    at, as_ = _aggs(ts), _aggs(st)
    if len(at) == len(as_) and at and all(a.arg == b.arg for a, b in zip(at, as_)):
        for a, b in zip(at, as_):
            if (a.fn, a.distinct) != (b.fn, b.distinct):
                fa = f"{a.fn}{' DISTINCT' if a.distinct else ''}"
                fb = f"{b.fn}{' DISTINCT' if b.distinct else ''}"
                return "AG-01", "FAIL", f"aggregates with {fa} in ThoughtSpot but {fb} in Strategy"
        for a, b in zip(at, as_):
            if a.level != b.level:
                if b.level is None and "metric levels" in st_lossy:
                    return "AG-02", "UNVERIFIED", "level not available in this Strategy input format"
                la = f"{a.level.kind} {list(a.level.dims)}" if a.level else "query level"
                lb = f"{b.level.kind} {list(b.level.dims)}" if b.level else "query level"
                return "AG-02", "FAIL", f"computed at {la} in ThoughtSpot but {lb} in Strategy"
        for a, b in zip(at, as_):
            if a.condition != b.condition:
                return "FL-02", "FAIL", "the aggregate's condition differs"
    rt, pt = _nonlinear(ts)
    rs, ps = _nonlinear(st)
    if (rt, pt) != (rs, ps) and (rt or rs) and set(columns(ts)) == set(columns(st)):
        where_t = "per row" if rt else "after aggregation"
        where_s = "per row" if rs else "after aggregation"
        return "AG-03", "FAIL", f"ratio/product computed {where_t} in ThoughtSpot but {where_s} in Strategy"
    if _strip_safe_divide(ts) == _strip_safe_divide(st):
        zt = "0" if ts != _strip_safe_divide(ts) else "NULL"
        zs = "0" if st != _strip_safe_divide(st) else "NULL"
        return "FX-03", "FAIL", f"division by zero gives {zt} in ThoughtSpot but {zs} in Strategy"
    return "FX-01", "UNVERIFIED", "formulas differ in form; equivalence not provable by rules"
