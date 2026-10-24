"""Strategy Mosaic model -> IR.

One code path for the model YAML export (``POST /api/model/dataModels/{id}/export``) and the
per-object REST JSON (``GET /api/model/dataModels/{id}/tables|attributes|factMetrics|metrics|hierarchy``):
both use the same object shapes. Strategy joins are *derived* (shared attribute forms), so this parser
records attribute placements and leaves ``joins`` to the resolver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from inspector_contract import (
    Agg, Col, ColumnDef, Expr, Field, Level, LogicalTable, ParseIssue, PhysicalTable, Placement, Relation,
    Relationship, SemanticModel, SourceRef,
)
from inspector_contract.ir import Opaque
from ..sql_analyzer.lineage import analyze
from . import expression as mstr_expr
from ..common.yamlio import Locator, load_json, load_yaml, plain

FUNCTIONS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "average": "AVG", "min": "MIN", "max": "MAX",
             "stdev": "STDDEV", "var": "VARIANCE", "median": "MEDIAN"}


class _Ctx:
    """Locators per section: one file for the YAML export, one file per object type for REST JSON."""

    def __init__(self, locators: dict[str, Locator], base_paths: dict[str, str]):
        self.locators = locators
        self.base_paths = base_paths

    def ref(self, section: str, index: int | None, node: Any, obj: dict | None = None) -> SourceRef:
        loc = self.locators.get(section) or self.locators["model"]
        path = self.base_paths.get(section, section)
        if index is not None:
            path = f"{path}[{index}]"
        object_id = ((obj or {}).get("information") or {}).get("objectId")
        return loc.ref(path, node, index, object_id=object_id)


def _relation(physical: dict) -> Relation:
    ptype = str(physical.get("type", "normal")).lower()
    if ptype == "sql" or physical.get("sqlStatement"):
        sql = str(physical.get("sqlStatement", ""))
        return Relation(kind="sql", sql=sql, lineage=analyze(sql))
    if ptype == "pipeline":
        src = _pipeline_source(physical.get("pipeline") or {})
        if src.get("sql"):
            sql = str(src["sql"])
            return Relation(kind="sql", sql=sql, lineage=analyze(sql))
        return Relation(kind="physical", table=PhysicalTable(namespace=str(src.get("namespace", "public")).lower(),
                                                             table=str(src.get("tableName", "")).lower()))
    return Relation(kind="physical", table=PhysicalTable(namespace=str(physical.get("namespace", "public")).lower(),
                                                         table=str(physical.get("tableName", "")).lower()))


def _pipeline_source(pipeline: dict) -> dict:
    stack = [pipeline.get("rootTable") or {}]
    while stack:
        node = stack.pop()
        if node.get("importSource"):
            return node["importSource"]
        stack.extend(node.get("children") or [])
    return {}


def _column_resolver(table: str):
    return lambda name: Col(table=table, column=name)


def _expressions(expressions: list, parse_issues: list, src: SourceRef) -> list[Placement]:
    out = []
    for e in expressions or []:
        text = str((e.get("expression") or {}).get("text", ""))
        for t in e.get("tables") or []:
            table = str(t.get("name"))
            expr = mstr_expr.parse(text, _column_resolver(table))
            if isinstance(expr, Opaque):
                parse_issues.append(ParseIssue(severity="warning", message=f"expression {text!r}: {expr.reason}",
                                               source=src))
            out.append(Placement(table=table, expr=expr))
    return out


def _level(dimty: dict | None) -> Level | None:
    units = (dimty or {}).get("dimtyUnits") or []
    attrs = [str(u["target"]["name"]) for u in units if u.get("dimtyUnitType") == "attribute" and u.get("target")]
    has_report = any(u.get("dimtyUnitType") in ("report_base_level", "report_level") for u in units)
    ignore = any(str(u.get("filtering", "apply")) in ("ignore", "absolute_ignore") for u in units)
    if not attrs:
        return None if not ignore else Level(kind="report", filters="ignore")
    return Level(kind="include" if has_report else "fixed", dims=tuple(attrs),
                 filters="ignore" if ignore else "apply")


def build(model: dict, ctx: _Ctx, kind: str = "mosaic") -> tuple[SemanticModel, list[ParseIssue]]:
    issues: list[ParseIssue] = []
    info = model.get("information") or {}
    name = str(info.get("name", "model"))

    tables = []
    for i, t in enumerate(model.get("tables") or []):
        tname = str((t.get("information") or {}).get("name"))
        physical = t.get("physicalTable") or {}
        relation = _relation(physical)
        if relation.lineage and relation.lineage.errors:
            for err in relation.lineage.errors:
                issues.append(ParseIssue(severity="warning", message=f"{tname}: {err}", source=ctx.ref("tables", i, model.get("tables"), t)))
        columns = tuple(ColumnDef(name=str(c.get("columnName")), physical=str(c.get("columnName")).lower(),
                                  data_type=(c.get("dataType") or {}).get("type"))
                        for c in physical.get("columns") or [])
        tables.append(LogicalTable(id=tname, name=tname, relation=relation, columns=columns,
                                   source=ctx.ref("tables", i, model.get("tables"), t)))

    fields = []
    for i, a in enumerate(model.get("attributes") or []):
        aname = str((a.get("information") or {}).get("name"))
        src = ctx.ref("attributes", i, model.get("attributes"), a)
        forms = a.get("forms") or []
        key_id = (a.get("keyForm") or {}).get("id")
        form = next((f for f in forms if f.get("id") == key_id), forms[0] if forms else {})
        placements = _expressions(form.get("expressions"), issues, src)
        lookup = str(((a.get("attributeLookupTable") or form.get("lookupTable") or {}).get("name")) or "")
        main = next((p for p in placements if p.table == lookup), placements[0] if placements else None)
        expr = main.expr if main else Opaque(dialect="strategy", text=aname, reason="attribute has no expression")
        fields.append(Field(id=f"mstr:{name}:{aname}", name=aname, kind="ATTRIBUTE", expr=expr,
                            object_type="mstr:attribute", data_type=(form.get("dataType") or {}).get("type"),
                            description=str((a.get("information") or {}).get("description", "") or ""),
                            placements=tuple(placements), source=src))

    for i, m in enumerate(model.get("factMetrics") or []):
        mname = str((m.get("information") or {}).get("name"))
        src = ctx.ref("factMetrics", i, model.get("factMetrics"), m)
        fact = m.get("fact") or {}
        placements = _expressions(fact.get("expressions"), issues, src)
        props = {str(p.get("name", "")).lower(): str((p.get("value") or {}).get("value", "")).lower()
                 for p in m.get("functionProperties") or []}
        fn = FUNCTIONS.get(str(m.get("function", "sum")).lower(), str(m.get("function", "")).upper())
        arg: Expr = placements[0].expr if placements else Opaque(dialect="strategy", text=mname, reason="no fact expression")
        expr = Agg(fn=fn, arg=arg, distinct=props.get("distinct") == "true", level=_level(m.get("dimty")))
        fields.append(Field(id=f"mstr:{name}:{mname}", name=mname, kind="MEASURE", expr=expr,
                            object_type="mstr:fact_metric", data_type=(fact.get("dataType") or {}).get("type"),
                            description=str((m.get("information") or {}).get("description", "") or ""),
                            placements=tuple(placements), source=src))

    for i, m in enumerate(model.get("metrics") or []):
        mname = str((m.get("information") or {}).get("name"))
        src = ctx.ref("metrics", i, model.get("metrics"), m)
        text = str((m.get("expression") or {}).get("text", ""))
        expr = mstr_expr.parse(text)
        if isinstance(expr, Opaque):
            issues.append(ParseIssue(severity="warning", message=f"metric {mname!r}: {expr.reason}", source=src))
        fields.append(Field(id=f"mstr:{name}:{mname}", name=mname, kind="MEASURE", expr=expr,
                            object_type="mstr:metric",
                            description=str((m.get("information") or {}).get("description", "") or ""), source=src))

    relationships = tuple(
        Relationship(parent=str(r["parent"]["name"]), child=str(r["child"]["name"]),
                     table=str((r.get("relationshipTable") or {}).get("name", "")),
                     type=str(r.get("relationshipType", "")))
        for r in (model.get("hierarchy") or {}).get("relationships") or [])

    settings = {k: plain(model[k]) for k in ("dataServeMode", "autoJoin", "enableAutoHierarchyRelationships")
                if k in model}
    settings["advancedProperties"] = {str(p.get("name")): plain(p.get("value"))
                                      for p in model.get("advancedProperties") or []}
    sm = SemanticModel(id=f"mstr:{name}", name=name, tool="strategy", kind=kind, tables=tuple(tables),
                       fields=tuple(fields), relationships=relationships, settings=settings,
                       description=str(info.get("description", "") or ""),
                       source=ctx.ref("model", None, model, model))
    return sm, issues


def load_yaml_export(path: Path, root: Path | None = None) -> tuple[SemanticModel, list[ParseIssue]]:
    doc = load_yaml(path)
    loc = Locator(path, root)
    model = (doc.get("model") or [{}])[0]
    ctx = _Ctx({"model": loc}, {"model": "model[0]", **{s: f"model[0].{s}" for s in
                                                        ("tables", "attributes", "factMetrics", "metrics")}})
    sm, issues = build(model, ctx)
    return sm.model_copy(update={"settings": sm.settings | {"fingerprint": str(doc.get("fingerprint", ""))}}), issues


def load_rest_json(directory: Path, root: Path | None = None) -> tuple[SemanticModel, list[ParseIssue]]:
    files = {"model": "dataModel.json", "tables": "tables.json", "attributes": "attributes.json",
             "factMetrics": "factMetrics.json", "metrics": "metrics.json", "hierarchy": "hierarchy.json"}
    model: dict = {}
    locators: dict[str, Locator] = {}
    issues: list[ParseIssue] = []
    for section, fname in files.items():
        path = directory / fname
        if not path.exists():
            if section in ("model", "tables"):
                issues.append(ParseIssue(severity="error", message=f"missing {fname}",
                                         source=SourceRef(file=str(path))))
            continue
        doc = load_json(path)
        locators[section] = Locator(path, root)
        if section == "model":
            model.update(doc)
        elif section == "hierarchy":
            model["hierarchy"] = doc
        else:
            model[section] = doc.get(section, [])
    locators.setdefault("model", Locator(directory / files["model"], root))
    ctx = _Ctx(locators, {s: s for s in files})
    sm, more = build(model, ctx)
    return sm, issues + more
