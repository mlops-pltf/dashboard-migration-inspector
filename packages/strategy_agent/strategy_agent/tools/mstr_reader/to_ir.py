""".mstr -> Dashboard IR. Visualization units link to model fields by Strategy object id (Field.source.object_id),
falling back to the names the .mstr carries. Everything that cannot be expressed exactly becomes a ParseIssue."""

from __future__ import annotations

import re
from pathlib import Path

from inspector_contract import (
    Agg, BinOp, Case, Dashboard, DashboardFilter, Expr, Field, FieldUse, FilterSpec, Func, Lit, Opaque, ParseIssue,
    QuerySpec, Ref, SemanticModel, SourceRef, UnaryOp, Visualization,
)

from .decode import decode_file, render

AGG = {"Sum": "SUM", "Avg": "AVG", "Average": "AVG", "Count": "COUNT", "Min": "MIN", "Max": "MAX",
       "Median": "MEDIAN", "Stdev": "STDDEV", "StdevP": "STDDEV", "Var": "VARIANCE", "VarP": "VARIANCE"}
BINARY = {"+": "+", "-": "-", "*": "*", "/": "/", "=": "=", "<>": "!=", "<": "<", "<=": "<=", ">": ">",
          ">=": ">=", "And": "and", "Or": "or"}
UNARY = {"Not": "not", "IsNull": "is_null", "IsNotNull": "is_not_null"}
QUALIFICATIONS = {"Between", "NotBetween", "<", "<=", ">", ">=", "=", "<>"}  # selector functions that filter
FILTER_OPS = {"In": "in", "NotIn": "not in", "=": "=", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
              "Between": "between"}


class _Ctx:
    def __init__(self, r: dict, models: tuple[SemanticModel, ...], src: SourceRef):
        self.names = {g: o["name"] for g, o in r["objects"].items() if o["name"]}
        self.helpers = r["helpers"]
        self.by_id = {f.source.object_id: f for m in models for f in m.fields if f.source and f.source.object_id}
        self.model_of = {m.source.object_id: m.name for m in models if m.source and m.source.object_id}
        self.derived = {m["id"] for m in r["derived_metrics"]}  # defined in the dashboard, not in the model
        self.src, self.issues, self.unlinked = src, [], set()

    def warn(self, message: str, path: str = "") -> None:
        self.issues.append(ParseIssue(severity="warning", message=message, source=self.src.model_copy(update={"path": path})))

    def field_name(self, gid: str | None, alias: str | None = None) -> str:
        if gid in self.by_id:
            return self.by_id[gid].name
        if gid in self.derived:
            return self.names[gid]
        h = self.helpers.get(gid) if gid not in self.names else None
        if h and h.get("fn") == "Rank" and h["args"] and "ref" in h["args"][0]:
            return self.field_name(h["args"][0]["ref"])  # Rank helper behind a top-N: the ranked metric is shown
        if gid and self.by_id and gid not in self.unlinked:
            self.unlinked.add(gid)
            self.warn(f"object {self.names.get(gid, alias) or gid} ({gid}) is not in the model export")
        return self.names.get(gid) or alias or (gid or "?")

    def expr(self, n: dict | None, depth: int = 0) -> Expr:
        if n is None or depth > 20:
            return Opaque(dialect="mstr", text="?", reason="expression not decoded")
        if "ref" in n:
            g = n["ref"]
            if g not in self.by_id and g not in self.names and g in self.helpers:
                return self.expr(self.helpers[g], depth + 1)  # unnamed helper: inline its definition
            return Ref(name=self.field_name(g))
        if "const" in n:
            v = n["const"]
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                return Opaque(dialect="mstr", text=str(v), reason="constant type not decoded")
            return Lit(value=v, type="string" if isinstance(v, str) else "number")
        if "elements" in n:
            return Func(name="list", args=tuple(Lit(value=x) for x in n["elements"]))
        if "fn" not in n:
            return Opaque(dialect="mstr", text=render(n, self.names), reason=f"expression node {n.get('node')} not decoded")
        fn, args = n["fn"] or "?", [self.expr(a, depth + 1) for a in n["args"]]
        if re.fullmatch(r"[0-9A-F]{32}", fn):
            return Opaque(dialect="mstr", text=render(n, self.names), reason=f"unknown function {fn}")
        if fn in AGG and len(args) == 1:
            if fn == "Count":
                self.warn("Count: the Distinct parameter is not decoded from .mstr; confirm in Strategy")
            return Agg(fn=AGG[fn], arg=args[0])
        if fn in BINARY and len(args) >= 2:
            out = args[0]
            for a in args[1:]:
                out = BinOp(op=BINARY[fn], left=out, right=a)
            return out
        if fn in BINARY and len(args) == 1 and fn in ("And", "Or"):
            return args[0]
        if fn in UNARY and len(args) == 1:
            return UnaryOp(op=UNARY[fn], arg=args[0])
        if fn in ("Case", "If") and len(args) >= 2:
            pairs = tuple((args[i], args[i + 1]) for i in range(0, len(args) - 1, 2))
            return Case(whens=pairs, else_=args[-1] if len(args) % 2 else None)
        return Func(name=fn.lower(), args=tuple(args))

    def filter_specs(self, n: dict, unparsed: list[str]) -> list[FilterSpec]:
        """Qualifications -> FilterSpec where exact (attribute/metric vs constants), else readable text."""
        if n.get("fn") == "And":
            return [f for a in n["args"] for f in self.filter_specs(a, unparsed)]
        fn, args = n.get("fn"), n.get("args") or []
        if fn in FILTER_OPS and args and "ref" in args[0]:
            vals = []
            for a in args[1:]:
                if "elements" in a:
                    vals += a["elements"]
                elif "const" in a:
                    vals.append(str(a["const"]))
                else:
                    vals = None
                    break
            if vals is not None:
                return [FilterSpec(field=self.field_name(args[0]["ref"]), op=FILTER_OPS[fn], values=tuple(vals))]
        unparsed.append(render(n, self.names, self.helpers))
        return []


def load(path: Path, models: tuple[SemanticModel, ...] = (), root: Path | None = None) -> tuple[Dashboard, list[ParseIssue]]:
    r = decode_file(path)
    rel = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)
    ctx = _Ctx(r, models, SourceRef(file=rel, object_id=r["dashboard"]["id"]))
    no_template = [i for i in r["issues"] if i["kind"].startswith("layout item without template")]
    for i in r["issues"]:
        if i not in no_template:
            ctx.warn(f"{i['kind']}: " + ", ".join(f"{k}={v}" for k, v in i.items() if k != "kind"))
    if no_template:  # text/image/panel widgets; every checked data visualization had a template
        ctx.warn(f"{len(no_template)} layout items have no template (text, image or panel widgets): "
                 + ", ".join(i["key"] for i in no_template[:20]))

    fields = tuple(Field(id=f"mstr:dashboard:{r['dashboard']['id']}:{m['id']}", name=m["name"], kind="MEASURE",
                         expr=ctx.expr(m["tree"]), object_type="mstr:derived_metric",
                         source=ctx.src.model_copy(update={"path": f"derived_metrics/{m['name']}", "object_id": m["id"]}))
                   for m in r["derived_metrics"])
    vizzes = []
    for v in r["visualizations"]:
        if not v["decoded"]:
            continue
        units = v["rows"] + v["columns"] + [u for ax in v["other_axes"] for u in ax]
        name = {u["id"]: ctx.field_name(u["id"], u["alias"]) for u in units}
        unparsed: list[str] = []
        filters: list[FilterSpec] = []
        for n in v["view_filter_trees"]:
            filters += ctx.filter_specs(n, unparsed)
        top_n = None
        for n, text in zip(v["limit_trees"], v["limits"]):
            m = re.fullmatch(r"\(Rank\(\[.+\]\) <= (\d+)\)", text)
            if m and top_n is None:
                top_n = int(m.group(1))
            else:
                unparsed.append(f"metric limit: {text}")
        sort = tuple((name.get(next((g for g in s["by"] if g in name), None)) or s["by"][0],
                      "ASC" if s["order"] == "ascending" else "DESC") for s in v["sorts"] if s["by"])
        query = QuerySpec(
            dimensions=tuple(FieldUse(field=name[u["id"]]) for u in units if u["kind"] == "attribute" and u["id"]),
            measures=tuple(FieldUse(field=name[u["id"]]) for u in units if u["kind"] == "metric" and u["id"]),
            filters=tuple(filters), sort=sort, top_n=top_n, unparsed=tuple(unparsed))
        vizzes.append(Visualization(id=v["key"], name=v["name"], query=query, chart_type="other",
                                    source=ctx.src.model_copy(update={"path": f"visualizations/{v['key']}"})))

    filters = []
    for f in r["filters"]:
        selected = [x for x in f["selected"] if x != "(All)"]
        field = ctx.field_name(f["source"]) if f["source"] else f["name"]
        if selected:
            filters.append(DashboardFilter(field=field, op="in", values=tuple(selected)))
        elif quals := [x for x in f["functions"] if x in QUALIFICATIONS]:
            filters.append(DashboardFilter(field=field, op=quals[0].lower(), values=()))
            ctx.warn(f"filter {f['name']!r}: qualification values are not decoded ({', '.join(quals)})")

    dash = Dashboard(id=f"mstr:dashboard:{r['dashboard']['id']}", name=r["dashboard"]["name"] or path.stem,
                     tool="strategy", kind="dashboard",
                     models=tuple(ctx.model_of.get(d["id"], d["name"]) for d in r["datasets"]),
                     visualizations=tuple(vizzes), filters=tuple(filters), fields=fields, source=ctx.src)
    return dash, ctx.issues
