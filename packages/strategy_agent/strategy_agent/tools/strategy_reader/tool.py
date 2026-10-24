"""Tools: list_strategy_fields, get_strategy_object."""

from __future__ import annotations

from strands import tool

from inspector_contract import to_text

from ..model_export.export import export


def _model(exp, name: str | None = None):
    return next((m for m in exp.models if name is None or m.name == name), None)


def list_fields_impl(location: str, model: str | None = None) -> dict:
    m = _model(export(location), model)
    if m is None:
        return {"error": f"no Strategy model {model!r}"}
    return {"model": m.name, "kind": m.kind, "input_format_loses": list(m.lossy), "fields": [
        {"name": f.name, "kind": f.kind, "type": f.object_type.split(":")[-1], "definition": to_text(f.expr),
         "source": str(f.source) if f.source else None,
         **({"lives_on_tables": sorted({p.table for p in f.placements})} if len({p.table for p in f.placements}) > 1 else {}),
         **({"description": f.description} if f.description else {})}
        for f in m.fields]}


def get_object_impl(location: str, name: str) -> dict:
    exp = export(location)
    for m in exp.models:
        if m.name == name:
            return {"type": m.kind, "name": m.name, "description": m.description, "settings": m.settings,
                    "input_format_loses": list(m.lossy),
                    "tables": [{"name": t.name, "relation": "sql" if t.relation.kind == "sql" else str(t.relation.table)}
                               for t in m.tables],
                    "joins": [{"name": j.id, "from": j.left, "to": j.right, "type": j.type, "keys": list(j.keys),
                               "origin": j.origin, "applied": j.applied} for j in m.joins],
                    "hierarchy": [r.model_dump(mode="json") for r in m.relationships]}
        for t in m.tables:
            if t.name == name:
                out = {"type": "logical_table", "name": t.name, "source": str(t.source) if t.source else None,
                       "relation": "sql" if t.relation.kind == "sql" else str(t.relation.table),
                       "columns": [c.name for c in t.columns]}
                if t.relation.sql:
                    out["sql"] = t.relation.sql
                return out
        f = m.field(name)
        if f is not None:
            return {"type": f.object_type.split(":")[-1], "name": f.name, "kind": f.kind,
                    "definition": to_text(f.expr), "source": str(f.source) if f.source else None,
                    "placements": [{"table": p.table, "expression": to_text(p.expr)} for p in f.placements],
                    "description": f.description}
    for d in exp.dashboards:
        if d.name == name:
            return {"type": "dashboard", "name": d.name, "models": list(d.models),
                    "filters": [x.model_dump(mode="json") for x in d.filters],
                    "visualizations": [{"id": v.id, "name": v.name, "chart": v.chart_type,
                                        **v.query.model_dump(mode="json", exclude_defaults=True)}
                                       for v in d.visualizations]}
    return {"error": f"no Strategy object named {name!r}"}


@tool
def list_strategy_fields(location: str, model: str | None = None) -> dict:
    """List every object of a Strategy (Mosaic) model: attributes, fact metrics and metrics with their definition
    in IR form, source file:line, the tables a join-key attribute lives on, and what the input format loses.

    Args:
        location: Folder, zip or s3:// prefix with the Strategy export.
        model: Model name; defaults to the first one.
    """
    return list_fields_impl(location, model)


@tool
def get_strategy_object(location: str, name: str) -> dict:
    """Show one Strategy object: the model (settings incl. join semantics, tables, joins derived from attributes,
    hierarchy), a logical table (with its Freeform SQL), an attribute/metric (definition and placements), or a
    dashboard (filters and every visualization's query).

    Args:
        location: Folder, zip or s3:// prefix with the Strategy export.
        name: Object name.
    """
    return get_object_impl(location, name)
