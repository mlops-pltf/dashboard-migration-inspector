"""Tools: list_thoughtspot_fields, get_thoughtspot_object."""

from __future__ import annotations

from strands import tool

from inspector_contract import to_text

from ..model_export.export import export


def _model(exp, name: str | None = None):
    return next((m for m in exp.models if name is None or m.name == name), None)


def list_fields_impl(location: str, model: str | None = None) -> dict:
    m = _model(export(location), model)
    if m is None:
        return {"error": f"no ThoughtSpot model {model!r}"}
    return {"model": m.name, "kind": m.kind, "fields": [
        {"name": f.name, "kind": f.kind, "type": f.object_type.split(":")[-1], "definition": to_text(f.expr),
         "source": str(f.source) if f.source else None,
         **({"synonyms": list(f.synonyms)} if f.synonyms else {}),
         **({"description": f.description} if f.description else {})}
        for f in m.fields]}


def get_object_impl(location: str, name: str) -> dict:
    exp = export(location)
    for m in exp.models:
        if m.name == name:
            return {"type": m.kind, "name": m.name, "description": m.description,
                    "tables": [{"id": t.id, "table": t.name, "relation": str(t.relation.table) if t.relation.table
                                else "sql", **({"sql": t.relation.sql} if t.relation.sql else {})} for t in m.tables],
                    "joins": [{"name": j.id, "from": j.left, "to": j.right, "type": j.type, "keys": list(j.keys),
                               "cardinality": j.cardinality, "applied": j.applied,
                               "source": str(j.source) if j.source else None} for j in m.joins],
                    "filters": [{"filter": to_text(f.expr), "source": str(f.source) if f.source else None}
                                for f in m.filters],
                    "settings": m.settings}
        for t in m.tables:
            if name in (t.id, t.name):
                return {"type": "table", "id": t.id, "table": t.name,
                        "relation": t.relation.model_dump(mode="json", exclude={"lineage"}),
                        "columns": [c.model_dump(mode="json") for c in t.columns]}
        f = m.field(name)
        if f is not None:
            return {"type": f.object_type.split(":")[-1], "name": f.name, "kind": f.kind,
                    "definition": to_text(f.expr), "source": str(f.source) if f.source else None,
                    "description": f.description}
    for d in exp.dashboards:
        if d.name == name:
            return {"type": d.kind, "name": d.name, "models": list(d.models),
                    "source": str(d.source) if d.source else None,
                    "filters": [f.model_dump(mode="json") for f in d.filters],
                    "visualizations": [{"id": v.id, "name": v.name, "chart": v.chart_type,
                                        **v.query.model_dump(mode="json", exclude_defaults=True)}
                                       for v in d.visualizations]}
    return {"error": f"no ThoughtSpot object named {name!r}"}


@tool
def list_thoughtspot_fields(location: str, model: str | None = None) -> dict:
    """List every column of a ThoughtSpot worksheet/model: name, attribute or measure, column or formula, its
    definition in IR form (formulas with table paths) and the source file:line.

    Args:
        location: Folder, zip or s3:// prefix with the ThoughtSpot TML.
        model: Worksheet/model name; defaults to the first one.
    """
    return list_fields_impl(location, model)


@tool
def get_thoughtspot_object(location: str, name: str) -> dict:
    """Show one ThoughtSpot object: a worksheet/model (tables, joins with type, keys, cardinality and whether
    applied only when needed, filters, settings), a table or table path, a single column/formula, or a
    liveboard/answer (filters and every visualization's query).

    Args:
        location: Folder, zip or s3:// prefix with the ThoughtSpot TML.
        name: Object name.
    """
    return get_object_impl(location, name)
