"""ThoughtSpot TML -> IR.

Supported object types: table, sql_view, worksheet, model, liveboard, answer.

Worksheets address tables through *table paths* (``FACT_ORDERS_1``); each path becomes its own logical
table, so role-playing dimensions (two paths to DIM_DATE) stay distinct. Models address tables by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from inspector_contract import (
    Agg, BinOp, Col, ColumnDef, Dashboard, DashboardFilter, Expr, Field, Func, Join, Lit, LogicalTable,
    ModelFilter, ParseIssue, Relation, SemanticModel, UnaryOp, Visualization, has_aggregate, walk,
)
from inspector_contract.ir import Opaque, PhysicalTable, Ref
from ..sql_analyzer.lineage import analyze
from . import formula as ts_formula, search as ts_search
from ..common.yamlio import Locator, load_yaml

KINDS = ("table", "sql_view", "worksheet", "model", "liveboard", "answer")

JOIN_TYPES = {"INNER": "INNER", "LEFT_OUTER": "LEFT", "RIGHT_OUTER": "RIGHT", "OUTER": "FULL",
              "FULL_OUTER": "FULL", "LEFT": "LEFT", "RIGHT": "RIGHT", "FULL": "FULL"}
CARDINALITY = {"MANY_TO_ONE": "N:1", "ONE_TO_MANY": "1:N", "ONE_TO_ONE": "1:1", "MANY_TO_MANY": "N:M"}
AGGREGATIONS = {"SUM": "SUM", "AVERAGE": "AVG", "COUNT": "COUNT", "COUNT_DISTINCT": "COUNT_DISTINCT",
                "MIN": "MIN", "MAX": "MAX", "STD_DEVIATION": "STDDEV", "VARIANCE": "VARIANCE"}
CHARTS = {"COLUMN": "column", "STACKED_COLUMN": "column", "BAR": "bar", "STACKED_BAR": "bar", "LINE": "line",
          "AREA": "area", "PIE": "pie", "KPI": "kpi", "HEADLINE": "kpi"}


@dataclass
class TmlObject:
    kind: str
    name: str
    doc: Any  # the object body (e.g. doc["worksheet"])
    guid: str | None
    loc: Locator


@dataclass
class TmlBundle:
    objects: list[TmlObject] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    def of(self, kind: str) -> list[TmlObject]:
        return [o for o in self.objects if o.kind == kind]

    def get(self, kind: str, name: str) -> TmlObject | None:
        return next((o for o in self.objects if o.kind == kind and o.name == name), None)


def load(files: list[Path], root: Path | None = None) -> TmlBundle:
    bundle = TmlBundle()
    for path in sorted(files):
        loc = Locator(path, root)
        try:
            doc = load_yaml(path)
        except Exception as exc:  # malformed YAML must not stop the package
            bundle.issues.append(ParseIssue(severity="error", message=f"invalid YAML: {exc}", source=loc.ref("")))
            continue
        kind = next((k for k in KINDS if isinstance(doc, dict) and k in doc), None)
        if kind is None:
            bundle.issues.append(ParseIssue(severity="warning", message="not a supported TML object",
                                            source=loc.ref("")))
            continue
        body = doc[kind]
        bundle.objects.append(TmlObject(kind, str(body.get("name", path.stem)), body, doc.get("guid"), loc))
    return bundle


# ---------------------------------------------------------------------------------------------
# Tables and SQL views
# ---------------------------------------------------------------------------------------------
def logical_table(obj: TmlObject, table_id: str | None = None) -> LogicalTable:
    d = obj.doc
    if obj.kind == "table":
        relation = Relation(kind="physical",
                            table=PhysicalTable(namespace=str(d.get("schema", "public")).lower(),
                                                table=str(d.get("db_table", obj.name)).lower()),
                            database=str(d["db"]).lower() if d.get("db") else None)
        columns = tuple(ColumnDef(name=str(c["name"]), physical=str(c.get("db_column_name", c["name"])).lower(),
                                  data_type=(c.get("db_column_properties") or {}).get("data_type"))
                        for c in d.get("columns", []))
    else:  # sql_view
        sql = str(d.get("sql_query", ""))
        relation = Relation(kind="sql", sql=sql, lineage=analyze(sql))
        columns = tuple(ColumnDef(name=str(c["name"]), physical=str(c.get("sql_output_column", c["name"])).lower())
                        for c in d.get("sql_view_columns", []))
    return LogicalTable(id=table_id or obj.name, name=obj.name, relation=relation, columns=columns,
                        source=obj.loc.ref(obj.kind, obj.doc, object_id=obj.guid))


def _base_object(bundle: TmlBundle, name: str) -> TmlObject | None:
    return bundle.get("table", name) or bundle.get("sql_view", name)


def _join_keys(on: str, left: str, right: str, issues: list, src) -> tuple[tuple[str, str], ...]:
    """'[A::x] = [B::y] AND ...' -> ((x, y), ...) oriented left -> right."""
    expr = ts_formula.parse(on)
    keys = []
    for node in walk(expr):
        if isinstance(node, BinOp) and node.op == "=" and isinstance(node.left, Col) and isinstance(node.right, Col):
            a, b = node.left, node.right
            if a.table == right and b.table == left:
                a, b = b, a
            keys.append((a.column, b.column))
    if not keys:
        issues.append(ParseIssue(severity="warning", message=f"could not read join condition {on!r}", source=src))
    return tuple(keys)


# ---------------------------------------------------------------------------------------------
# Worksheets and models
# ---------------------------------------------------------------------------------------------
def _measure_expr(expr: Expr, aggregation: str | None) -> Expr:
    if has_aggregate(expr):
        return expr
    fn = AGGREGATIONS.get(str(aggregation or "SUM").upper(), "SUM")
    distinct = fn == "COUNT_DISTINCT"
    return Agg(fn="COUNT" if distinct else fn, arg=expr, distinct=distinct)


def _filter_expr(column: Expr, oper: str, values: list) -> Expr:
    lits = tuple(Lit(value=str(v)) for v in values)
    oper = oper.lower()
    if oper in ("in", "not in"):
        e = Func(name="in", args=(column, *lits))
        return UnaryOp(op="not", arg=e) if oper == "not in" else e
    if oper == "between" and len(lits) == 2:
        return Func(name="between", args=(column, *lits))
    op = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}.get(oper, oper)
    return BinOp(op=op, left=column, right=lits[0] if lits else Lit(value=None, type="null"))


def parse_model(obj: TmlObject, bundle: TmlBundle) -> tuple[SemanticModel, list[ParseIssue]]:
    d, loc, issues = obj.doc, obj.loc, []
    # progressive joins (the default): a join is applied only when a query uses the joined table
    applied = "when_needed" if bool((d.get("properties") or {}).get("join_progressive", True)) else "always"
    tables: list[LogicalTable] = []
    joins: list[Join] = []
    table_of: dict[str, str] = {}  # logical id -> base object name

    if obj.kind == "worksheet":
        declared = {str(j["name"]): j for j in d.get("joins", [])}
        paths = list(d.get("table_paths", []))
        chain_to_path: dict[tuple, str] = {}
        for p in paths:
            chain = tuple(step for part in p.get("join_path", [{}]) for step in (part or {}).get("join", []))
            chain_to_path[chain] = str(p["id"])
        for i, p in enumerate(paths):
            pid, name = str(p["id"]), str(p["table"])
            base = _base_object(bundle, name)
            if base is None:
                issues.append(ParseIssue(severity="error", message=f"table {name!r} not in package",
                                         source=loc.ref(f"worksheet.table_paths[{i}]", d["table_paths"], i)))
                continue
            tables.append(logical_table(base, pid))
            table_of[pid] = name
        for chain, pid in chain_to_path.items():
            if not chain:
                continue
            jname = chain[-1]
            parent = chain_to_path.get(chain[:-1])
            source_table = table_of.get(parent) if parent else None
            jw = _find_joins_with(bundle, jname)
            ws_join = declared.get(jname, {})
            on = jw.get("on", "") if jw else ""
            jtype = JOIN_TYPES.get(str(ws_join.get("type") or (jw or {}).get("type", "INNER")).upper(), "INNER")
            card = "1:1" if (ws_join.get("is_one_to_one") or (jw or {}).get("is_one_to_one")) else "N:1"
            src = loc.ref("worksheet.joins", d.get("joins"))
            keys = _join_keys(on, source_table, table_of.get(pid), issues, src) if on else ()
            if parent is None:
                issues.append(ParseIssue(severity="warning", message=f"join path for {pid} has no parent", source=src))
                continue
            joins.append(Join(id=jname, left=parent, right=pid, type=jtype, keys=keys, cardinality=card,
                              applied=applied, source=src))
        resolve_table = {name: pid for pid, name in table_of.items()}  # base name -> first path (formulas use path ids)

        def resolver(table: str | None, column: str) -> Expr:
            if table is None:
                return Ref(name=column)
            return Col(table=table if table in table_of else resolve_table.get(table, table), column=column)

        columns_key, formulas_key, settings = "worksheet_columns", "formulas", dict(d.get("properties") or {})
    else:  # model
        for i, mt in enumerate(d.get("model_tables", [])):
            name = str(mt["name"])
            base = _base_object(bundle, name)
            if base is None:
                issues.append(ParseIssue(severity="error", message=f"table {name!r} not in package",
                                         source=loc.ref(f"model.model_tables[{i}]", d["model_tables"], i)))
                continue
            tid = str(mt.get("alias") or name)
            tables.append(logical_table(base, tid))
            table_of[tid] = name
        for i, mt in enumerate(d.get("model_tables", [])):
            left = str(mt.get("alias") or mt["name"])
            for k, j in enumerate(mt.get("joins", []) or []):
                right = str(j["with"])
                src = loc.ref(f"model.model_tables[{i}].joins[{k}]", mt["joins"], k)
                joins.append(Join(id=str(j.get("name") or f"{left}_to_{right}"), left=left, right=right,
                                  type=JOIN_TYPES.get(str(j.get("type", "INNER")).upper(), "INNER"),
                                  keys=_join_keys(str(j.get("on", "")), left, right, issues, src),
                                  cardinality=CARDINALITY.get(str(j.get("cardinality", "")).upper(), "unknown"),
                                  applied=applied, source=src))

        def resolver(table: str | None, column: str) -> Expr:
            return Col(table=table, column=column) if table else Ref(name=column)

        columns_key, formulas_key, settings = "columns", "formulas", dict(d.get("properties") or {})

    formulas = {}
    for i, f in enumerate(d.get(formulas_key, []) or []):
        expr = ts_formula.parse(str(f.get("expr", "")), resolver)
        src = loc.ref(f"{obj.kind}.formulas[{i}]", d[formulas_key], i)
        if isinstance(expr, Opaque):
            issues.append(ParseIssue(severity="warning", message=f"formula {f.get('name')!r}: {expr.reason}", source=src))
        formulas[str(f.get("id") or f.get("name"))] = (expr, src)

    fields = []
    for i, c in enumerate(d.get(columns_key, []) or []):
        props = c.get("properties") or {}
        kind = str(props.get("column_type", "ATTRIBUTE")).upper()
        src = loc.ref(f"{obj.kind}.{columns_key}[{i}]", d[columns_key], i)
        if c.get("formula_id") is not None:
            expr, src = formulas.get(str(c["formula_id"]), (Opaque(dialect="thoughtspot", text=str(c["formula_id"]),
                                                                   reason="formula not found"), src))
            object_type = "ts:formula"
            if "column_type" not in props:
                kind = "MEASURE" if has_aggregate(expr) else "ATTRIBUTE"
        else:
            table, _, column = str(c.get("column_id", "")).rpartition("::")
            expr = resolver(table, column)
            object_type = "ts:column"
        if kind == "MEASURE":
            expr = _measure_expr(expr, props.get("aggregation"))
        fields.append(Field(id=f"ts:{obj.name}:{c['name']}", name=str(c["name"]), kind=kind, expr=expr,
                            object_type=object_type, description=str(c.get("description", "") or ""),
                            synonyms=tuple(str(s) for s in props.get("synonyms", []) or []),
                            hidden=bool(props.get("is_hidden", False)), source=src))

    filters = []
    for i, f in enumerate(d.get("filters", []) or []):
        cols = f.get("column") or []
        table, _, column = str(cols[0] if cols else "").rpartition("::")
        filters.append(ModelFilter(expr=_filter_expr(resolver(table or None, column), str(f.get("oper", "in")),
                                                     list(f.get("values", []) or [])),
                                   source=loc.ref(f"{obj.kind}.filters[{i}]", d["filters"], i)))

    settings.setdefault("join_progressive", True)
    model = SemanticModel(id=f"ts:{obj.name}", name=obj.name, tool="thoughtspot", kind=obj.kind,
                          tables=tuple(tables), joins=tuple(joins), fields=tuple(fields), filters=tuple(filters),
                          settings={k: v for k, v in settings.items()}, description=str(d.get("description", "") or ""),
                          source=loc.ref(obj.kind, d, object_id=obj.guid))
    return model, issues


def _find_joins_with(bundle: TmlBundle, name: str) -> dict | None:
    for t in bundle.of("table") + bundle.of("sql_view"):
        for j in t.doc.get("joins_with", []) or []:
            if str(j.get("name")) == name:
                return j
    return None


# ---------------------------------------------------------------------------------------------
# Liveboards and answers
# ---------------------------------------------------------------------------------------------
def _visualization(answer: Any, viz_id: str, models: dict[str, SemanticModel], loc: Locator, path: str,
                   node: Any) -> tuple[Visualization, tuple[str, ...]]:
    model_names = tuple(str(t.get("name") or t.get("id")) for t in answer.get("tables", []) or [])
    model = next((models[m] for m in model_names if m in models), None)

    def kind_of(name: str) -> str | None:
        f = model.field(name) if model else None
        return f.kind if f else None

    query = ts_search.parse(str(answer.get("search_query", "")), kind_of)
    chart = answer.get("chart") or {}
    if str(answer.get("display_mode", "")).upper() == "TABLE_MODE" or not chart:
        chart_type = "table"
    else:
        chart_type = CHARTS.get(str(chart.get("type", "")).upper(), "other")
    return Visualization(id=viz_id, name=str(answer.get("name", viz_id)), query=query, chart_type=chart_type,
                         source=loc.ref(path, node)), model_names


def parse_content(obj: TmlObject, models: dict[str, SemanticModel]) -> Dashboard:
    d, loc = obj.doc, obj.loc
    vizs, used = [], []
    if obj.kind == "answer":
        v, names = _visualization(d, "answer", models, loc, "answer", d)
        vizs.append(v)
        used.extend(names)
        filters = ()
    else:
        for i, item in enumerate(d.get("visualizations", []) or []):
            v, names = _visualization(item.get("answer") or {}, str(item.get("id", f"Viz_{i + 1}")), models, loc,
                                      f"liveboard.visualizations[{i}]", d["visualizations"][i])
            vizs.append(v)
            used.extend(n for n in names if n not in used)
        filters = tuple(
            DashboardFilter(field=str((f.get("column") or [""])[0]), op=str(f.get("oper", "in")).lower(),
                            values=tuple(str(x) for x in f.get("values", []) or []),
                            scope=tuple(str(x) for x in f.get("visualization_ids", []) or []))
            for f in d.get("filters", []) or [])
    return Dashboard(id=f"ts:{obj.kind}:{obj.name}", name=obj.name, tool="thoughtspot", kind=obj.kind,
                     models=tuple(dict.fromkeys(used)), visualizations=tuple(vizs), filters=filters,
                     source=loc.ref(obj.kind, d, object_id=obj.guid))
