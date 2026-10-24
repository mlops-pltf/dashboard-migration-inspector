"""ThoughtSpot folder -> ModelExport (IR). Deterministic: the same files always give the same export."""

from __future__ import annotations

import functools
from pathlib import Path

from inspector_contract import Col, Field, ModelExport, ParseIssue, SemanticModel

from ..common.locations import materialize
from ..thoughtspot_reader import tml


def _sql_view_model(obj: tml.TmlObject) -> SemanticModel:
    """A SQL view used directly as a data source: one table, one field per output column."""
    table = tml.logical_table(obj)
    fields = tuple(Field(id=f"ts:{obj.name}:{c.name}", name=c.name, kind="ATTRIBUTE",
                         expr=Col(table=table.id, column=c.name), object_type="ts:column") for c in table.columns)
    return SemanticModel(id=f"ts:{obj.name}", name=obj.name, tool="thoughtspot", kind="sql_view",
                         tables=(table,), fields=fields, source=table.source)


@functools.lru_cache(maxsize=32)
def _build(path: str, fingerprint: str, location: str) -> ModelExport:
    root = Path(path)
    bundle = tml.load(sorted(root.rglob("*.tml")), root)
    issues = list(bundle.issues)
    models = []
    for obj in bundle.of("worksheet") + bundle.of("model"):
        model, more = tml.parse_model(obj, bundle)
        models.append(model)
        issues.extend(more)
    models.extend(_sql_view_model(obj) for obj in bundle.of("sql_view"))
    by_name = {m.name: m for m in models}
    dashboards = [tml.parse_content(o, by_name) for o in bundle.of("liveboard") + bundle.of("answer")]
    sql_dir = root / "sql"
    sql = {str(p.relative_to(sql_dir).with_suffix("")): p.read_text() for p in sorted(sql_dir.rglob("*.sql"))} \
        if sql_dir.exists() else {}
    if not models:
        issues.append(ParseIssue(severity="error", message="no ThoughtSpot worksheet, model or SQL view found"))
    return ModelExport(tool="thoughtspot", location=location, fingerprint=fingerprint, source_format="tml",
                       models=tuple(models), dashboards=tuple(dashboards), sql=sql, issues=tuple(issues),
                       status="error" if any(i.severity == "error" for i in issues) else "ok")


def export(location: str) -> ModelExport:
    path, fingerprint = materialize(location)
    return _build(str(path), fingerprint, location)
