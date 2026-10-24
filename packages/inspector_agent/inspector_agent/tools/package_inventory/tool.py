"""Tool: inspect_package."""

from __future__ import annotations

from strands import tool

from .loader import load_cached


def inspect_package_impl(package: str) -> dict:
    pkg = load_cached(package, refresh=True)  # a new look always re-asks the experts

    def models(side):
        out = []
        for m in side.models:
            out.append({
                "name": m.name, "kind": m.kind,
                "tables": [{"name": t.name, "source": "sql" if t.relation.kind == "sql" else str(t.relation.table)}
                           for t in m.tables],
                "joins": [f"{j.left} {j.type} {j.right} ({j.origin}, applied {j.applied})" for j in m.joins],
                "attributes": sum(f.kind == "ATTRIBUTE" for f in m.fields),
                "measures": sum(f.kind == "MEASURE" for f in m.fields),
                "model_filters": len(m.filters),
                "settings": {k: v for k, v in m.settings.items() if k != "fingerprint"},
                "input_format_loses": list(m.lossy),
            })
        return out

    def dashboards(side):
        return [{"name": d.name, "kind": d.kind, "models": list(d.models), "visualizations": len(d.visualizations),
                 "filters": len(d.filters)} for d in side.dashboards]

    mapping = None
    if pkg.mapping:
        mapping = {"models": [e.model_dump() for e in pkg.mapping.models],
                   "fields": [e.model_dump() for e in pkg.mapping.fields],
                   "ignore": [e.model_dump() for e in pkg.mapping.ignore]}
    return {
        "package_id": pkg.id,
        "strategy_format": pkg.strategy_format,
        "thoughtspot": {"models": models(pkg.thoughtspot), "dashboards": dashboards(pkg.thoughtspot),
                        "sql_supplied": sorted(pkg.thoughtspot.sql)},
        "strategy": {"models": models(pkg.strategy), "dashboards": dashboards(pkg.strategy),
                     "sql_supplied": sorted(pkg.strategy.sql)},
        "mapping": mapping,
        "parse_issues": [{"severity": i.severity, "message": i.message, "source": str(i.source) if i.source else None}
                         for i in pkg.issues],
    }


@tool
def inspect_package(package: str) -> dict:
    """Open a migration package: the ThoughtSpot and Strategy experts export their side as IR, and this lists what
    is inside: models (tables, joins, number of attributes/measures, model filters, settings, what the input
    format cannot carry), dashboards, supplied SQL, the mapping file and any problems. Start every validation
    with this; it always fetches fresh exports.

    Args:
        package: Path of the package folder or zip.
    """
    return inspect_package_impl(package)
