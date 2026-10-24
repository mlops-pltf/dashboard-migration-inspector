"""Tools: list_fields, get_object. Browse either side of the package, as the experts exported it (IR)."""

from __future__ import annotations

from strands import tool

from inspector_contract import to_text

from ..package_inventory.loader import load_cached

SIDES = ("thoughtspot", "strategy")


def _side(package: str, side: str):
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    pkg = load_cached(package)
    return pkg.thoughtspot if side == "thoughtspot" else pkg.strategy


def list_fields_impl(package: str, side: str) -> dict:
    s = _side(package, side)
    if not s.models:
        return {"error": f"no {side} model in the package"}
    m = s.models[0]
    return {"side": side, "model": m.name, "kind": m.kind, "input_format_loses": list(m.lossy), "fields": [
        {"name": f.name, "kind": f.kind, "type": f.object_type.split(":")[-1], "definition": to_text(f.expr),
         "source": str(f.source) if f.source else None,
         **({"provenance": f.provenance} if f.provenance != "parsed" else {}),
         **({"lives_on_tables": sorted({p.table for p in f.placements})} if len({p.table for p in f.placements}) > 1 else {}),
         **({"synonyms": list(f.synonyms)} if f.synonyms else {}),
         **({"description": f.description} if f.description else {})} for f in m.fields]}


def get_object_impl(package: str, side: str, name: str) -> dict:
    s = _side(package, side)
    for m in s.models:
        if m.name == name:
            return {"type": m.kind, "name": m.name, "description": m.description, "settings": m.settings,
                    "input_format_loses": list(m.lossy),
                    "tables": [{"id": t.id, "relation": "sql" if t.relation.kind == "sql" else str(t.relation.table)}
                               for t in m.tables],
                    "joins": [{"name": j.id, "from": j.left, "to": j.right, "type": j.type, "keys": list(j.keys),
                               "cardinality": j.cardinality, "origin": j.origin, "applied": j.applied,
                               "source": str(j.source) if j.source else None} for j in m.joins],
                    "filters": [{"filter": to_text(f.expr), "source": str(f.source) if f.source else None}
                                for f in m.filters]}
        for t in m.tables:
            if name in (t.id, t.name):
                out = {"type": "table", "id": t.id, "name": t.name, "source": str(t.source) if t.source else None,
                       "relation": "sql" if t.relation.kind == "sql" else str(t.relation.table),
                       "columns": [c.name for c in t.columns]}
                if t.relation.sql:
                    out["sql"] = t.relation.sql
                return out
        f = m.field(name)
        if f is not None:
            return {"type": f.object_type, "name": f.name, "kind": f.kind, "definition": to_text(f.expr),
                    "source": str(f.source) if f.source else None, "provenance": f.provenance,
                    "placements": [{"table": p.table, "expression": to_text(p.expr)} for p in f.placements],
                    "description": f.description}
    for d in s.dashboards:
        if d.name == name:
            return {"type": d.kind, "name": d.name, "models": list(d.models),
                    "filters": [x.model_dump(mode="json") for x in d.filters],
                    "visualizations": [{"id": v.id, "name": v.name, "chart": v.chart_type,
                                        **v.query.model_dump(mode="json", exclude_defaults=True)} for v in d.visualizations]}
    return {"error": f"no {side} object named {name!r}"}


@tool
def list_fields(package: str, side: str) -> dict:
    """List every field of one side's model as its expert exported it: name, attribute or measure, definition,
    source file:line, tables a join-key attribute lives on, and what the input format loses.

    Args:
        package: The package location.
        side: "thoughtspot" or "strategy".
    """
    return list_fields_impl(package, side)


@tool
def get_object(package: str, side: str, name: str) -> dict:
    """Show one object of one side: the model (settings, tables, joins with type/keys/cardinality/when applied,
    filters), a table (with its SQL if SQL-backed), a field (definition, placements, provenance), or a dashboard
    (filters and every visualization's query).

    Args:
        package: The package location.
        side: "thoughtspot" or "strategy".
        name: Object name.
    """
    return get_object_impl(package, side, name)
