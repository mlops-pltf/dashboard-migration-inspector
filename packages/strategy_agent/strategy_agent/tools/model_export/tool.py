"""Tool: summarize_export (the full export is the structured A2A skill export_model)."""

from __future__ import annotations

from strands import tool

from .export import export


def summarize_export_impl(location: str) -> dict:
    exp = export(location)
    return {
        "status": exp.status, "fingerprint": exp.fingerprint, "format": exp.source_format,
        "models": [{"name": m.name, "kind": m.kind, "tables": [t.id for t in m.tables],
                    "joins": [f"{j.left} {j.type} {j.right} ({j.applied})" for j in m.joins],
                    "attributes": sum(f.kind == "ATTRIBUTE" for f in m.fields),
                    "measures": sum(f.kind == "MEASURE" for f in m.fields),
                    "filters": len(m.filters), "settings": m.settings,
                    "input_format_loses": list(m.lossy)} for m in exp.models],
        "dashboards": [{"name": d.name, "kind": d.kind, "visualizations": len(d.visualizations)} for d in exp.dashboards],
        "issues": [{"severity": i.severity, "message": i.message, "source": str(i.source) if i.source else None}
                   for i in exp.issues],
    }


@tool
def summarize_export(location: str) -> dict:
    """Read a Strategy export folder (model YAML export, REST JSON or Ossie YAML, plus dashboards/*.mstr) and summarise it: models with
    their tables, joins (type and whether applied only when needed), field counts, filters, settings,
    dashboards and parse problems.

    Args:
        location: Folder, zip or s3:// prefix with the Strategy export.
    """
    return summarize_export_impl(location)
