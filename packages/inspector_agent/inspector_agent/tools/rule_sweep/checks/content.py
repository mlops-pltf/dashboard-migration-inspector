"""DB-01 / DB-02 / DB-05 / DB-06 / LF-01 / LF-02: dashboards (ThoughtSpot liveboards and answers vs Strategy dashboards).

Dashboards pair by name; visualizations by mapping.yaml, then title, then identical query (a renamed title).
ThoughtSpot field names are translated to their Strategy counterparts first, so model renames are not content
differences. What the Strategy side could not read (QuerySpec.unparsed) is UNVERIFIED, never PASS.
"""

from __future__ import annotations

from inspector_contract import Dashboard, DashboardFilter, FilterSpec, Visualization
from ...findings_ledger.report import Finding, ObjectRef

from ..context import Context

UNKNOWN_CHART = "other"


def _key(name: str) -> str:
    return " ".join(name.casefold().split())


class _Names:
    """ThoughtSpot field name -> Strategy field name: the counterpart, the same name for fields of other models,
    or a placeholder that matches nothing when the field has no counterpart."""

    def __init__(self, ctx: Context):
        self.to_st = {m.ts: m.st for m in ctx.matches if m.ts}

    def __call__(self, ts_name: str) -> str:
        if ts_name in self.to_st:
            return self.to_st[ts_name] or f"{ts_name} (no Strategy counterpart)"
        return ts_name


def _ref(d: Dashboard, name: str, kind: str = "visualization") -> ObjectRef:
    return ObjectRef(model=d.name, name=name, kind=kind, location=str(d.source) if d.source else None)


def _pairs(ctx: Context) -> list[tuple[Dashboard, Dashboard]]:
    st = {_key(d.name): d for d in ctx.package.strategy.dashboards}
    return [(d, st[_key(d.name)]) for d in ctx.package.thoughtspot.dashboards if _key(d.name) in st]


def _signature(v: Visualization, translate=lambda n: n) -> tuple:
    q = v.query
    return (frozenset(translate(u.field) for u in q.dimensions), frozenset(translate(u.field) for u in q.measures))


def _viz_pairs(ctx: Context, td: Dashboard, sd: Dashboard, names: _Names):
    mapped = {}
    for e in (ctx.package.mapping.visualizations if ctx.package.mapping else ()):
        t, s = e.thoughtspot.split("::")[-1], e.strategy.split("::")[-1]
        mapped[_key(t)] = _key(s)
    left = list(sd.visualizations)
    out = []
    for tv in td.visualizations:
        want = mapped.get(_key(tv.name), _key(tv.name))
        sv = next((v for v in left if _key(v.name) == want), None)
        if sv is None:  # a renamed title: the one Strategy visualization with the same query
            same = [v for v in left if _signature(v) == _signature(tv, names)]
            sv = same[0] if len(same) == 1 else None
        if sv is not None:
            left.remove(sv)
        out.append((tv, sv))
    return out


def _fields(td, tv, sd, sv, names, out):
    for label, tq, sq in (("dimension", tv.query.dimensions, sv.query.dimensions),
                          ("measure", tv.query.measures, sv.query.measures)):
        want = {names(u.field): u for u in tq}
        have = {u.field: u for u in sq}
        missing = [k if k == u.field or k.endswith("counterpart)") else f"{u.field} (Strategy: {k})"
                   for k, u in want.items() if k not in have]
        extra = [k for k in have if k not in want]
        if missing or extra:
            out.append(Finding(
                check="DB-01", severity="BLOCKER", status="FAIL",
                title=f"{td.name} / {tv.name}: {label}s differ"
                      + (f"; missing in Strategy: {', '.join(missing)}" if missing else "")
                      + (f"; only in Strategy: {', '.join(extra)}" if extra else ""),
                thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name),
                evidence={"thoughtspot": ", ".join(u.field for u in tq), "strategy": ", ".join(have)},
                suggested_fix=f"Make the Strategy visualization use the same {label}s."))
        for k, u in want.items():
            s = have.get(k)
            if s is not None and (u.aggregation or None) != (s.aggregation or None):
                out.append(Finding(check="DB-06", severity="BLOCKER", status="FAIL",
                                   title=f"{td.name} / {tv.name}: aggregation of {u.field} differs",
                                   thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name),
                                   evidence={"thoughtspot": u.aggregation or "default", "strategy": s.aggregation or "default"}))


def _limits(td, tv, sd, sv, names, out):
    tq, sq = tv.query, sv.query
    t_sort = tuple((names(f), o) for f, o in tq.sort)
    if tq.top_n != sq.top_n or ((tq.top_n or sq.top_n) and t_sort != sq.sort):
        out.append(Finding(check="DB-05", severity="BLOCKER", status="FAIL",
                           title=f"{td.name} / {tv.name}: shown rows differ (top {tq.top_n} vs top {sq.top_n})",
                           thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name),
                           evidence={"thoughtspot": f"top {tq.top_n} by {t_sort}", "strategy": f"top {sq.top_n} by {sq.sort}"},
                           suggested_fix="Use the same top-N and sort in the Strategy visualization."))
    elif t_sort != sq.sort:
        out.append(Finding(check="LF-02", severity="LOW", status="FAIL",
                           title=f"{td.name} / {tv.name}: sort order differs (same rows)",
                           thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name),
                           evidence={"thoughtspot": str(t_sort), "strategy": str(sq.sort)}))


def _filter_key(f: FilterSpec | DashboardFilter, names=None) -> tuple:
    field = names(f.field) if names else f.field
    return field, f.op, frozenset(f.values)


def _viz_filters(td, tv, sd, sv, names, out):
    t = {_filter_key(f, names) for f in tv.query.filters}
    s = {_filter_key(f) for f in sv.query.filters}
    if t != s:
        out.append(Finding(check="DB-02", severity="BLOCKER", status="FAIL",
                           title=f"{td.name} / {tv.name}: visualization filters differ",
                           thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name),
                           evidence={"thoughtspot": _show(t), "strategy": _show(s)}))
    for text in sv.query.unparsed:
        out.append(Finding(check="DB-05" if text.startswith("metric limit") else "DB-02",
                           severity="BLOCKER", status="UNVERIFIED",
                           title=f"{td.name} / {sv.name}: Strategy condition the reader could not express: {text}",
                           thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name),
                           evidence={"strategy": text}, needs=["llm:compare_condition"]))


def _show(keys) -> str:
    return "; ".join(f"{f} {op} ({', '.join(sorted(v))})" for f, op, v in sorted(keys, key=str)) or "none"


def _dashboard_filters(td, sd, names, out):
    t = {names(f.field): f for f in td.filters}
    s = {f.field: f for f in sd.filters}
    for field in sorted(set(t) | set(s), key=str):
        a, b = t.get(field), s.get(field)
        if a and b and a.op == b.op and set(a.values) == set(b.values):
            continue
        if b and a and not b.values and b.op != "in":
            out.append(Finding(check="DB-02", severity="BLOCKER", status="UNVERIFIED",
                               title=f"{td.name}: filter {field}: Strategy {b.op} qualification, values not readable",
                               thoughtspot=_ref(td, f"filter {a.field}", "filter"), strategy=_ref(sd, f"filter {field}", "filter"),
                               needs=["llm:compare_condition"]))
            continue
        out.append(Finding(
            check="DB-02", severity="BLOCKER", status="FAIL",
            title=f"{td.name}: filter {field}: " + (
                "missing in Strategy" if not b else "only in Strategy" if not a else
                f"{a.op} ({', '.join(a.values)}) vs {b.op} ({', '.join(b.values)})"),
            thoughtspot=_ref(td, f"filter {a.field}", "filter") if a else None,
            strategy=_ref(sd, f"filter {field}", "filter") if b else None,
            evidence={"thoughtspot": f"{a.op} {list(a.values)}" if a else "none",
                      "strategy": f"{b.op} {list(b.values)}" if b else "none"},
            suggested_fix="Set the same default filter values in the Strategy dashboard."))


def run(ctx: Context) -> list[Finding]:
    names = _Names(ctx)
    out: list[Finding] = []
    paired = _pairs(ctx)
    for td in ctx.package.thoughtspot.dashboards:
        if all(td is not p[0] for p in paired):
            out.append(Finding(check="DB-01", severity="BLOCKER", status="FAIL",
                               title=f"{td.name} has no Strategy dashboard of the same name",
                               thoughtspot=_ref(td, td.name, "dashboard"),
                               suggested_fix="Migrate it, or map it in mapping.yaml."))
    for td, sd in paired:
        _dashboard_filters(td, sd, names, out)
        for tv, sv in _viz_pairs(ctx, td, sd, names):
            if sv is None:
                out.append(Finding(check="DB-01", severity="BLOCKER", status="FAIL",
                                   title=f"{td.name} / {tv.name} has no counterpart in the Strategy dashboard",
                                   thoughtspot=_ref(td, tv.name)))
                continue
            if _key(tv.name) != _key(sv.name):
                out.append(Finding(check="LF-02", severity="LOW", status="FAIL",
                                   title=f"{td.name}: title {tv.name!r} vs {sv.name!r}",
                                   thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name)))
            if UNKNOWN_CHART not in (tv.chart_type, sv.chart_type) and tv.chart_type != sv.chart_type:
                out.append(Finding(check="LF-01", severity="LOW", status="FAIL",
                                   title=f"{td.name} / {tv.name}: chart {tv.chart_type} vs {sv.chart_type}",
                                   thoughtspot=_ref(td, tv.name), strategy=_ref(sd, sv.name)))
            _fields(td, tv, sd, sv, names, out)
            _limits(td, tv, sd, sv, names, out)
            _viz_filters(td, tv, sd, sv, names, out)
    return out
