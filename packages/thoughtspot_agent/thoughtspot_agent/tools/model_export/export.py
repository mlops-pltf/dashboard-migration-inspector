"""ThoughtSpot folder -> ModelExport (IR). Deterministic: the same files always give the same export."""

from __future__ import annotations

import functools
from pathlib import Path

from inspector_contract import Col, Field, ModelExport, ParseIssue, SemanticModel

from ..common.locations import materialize, package_files
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
    bundle = tml.load(package_files(root, "*.tml"), root)
    issues = list(bundle.issues)
    models, dashboards = [], []

    def guarded(obj, parse):  # one unreadable object must not hide the rest of the package
        try:
            return parse(obj)
        except Exception as exc:
            issues.append(ParseIssue(severity="error", source=obj.loc.ref(""),
                                     message=f"could not read {obj.kind} {obj.name!r}: {type(exc).__name__}: {exc}"))
            return None

    for obj in bundle.of("worksheet") + bundle.of("model"):
        if (got := guarded(obj, lambda o: tml.parse_model(o, bundle))) is not None:
            models.append(got[0])
            issues.extend(got[1])
    models.extend(m for o in bundle.of("sql_view") if (m := guarded(o, _sql_view_model)) is not None)
    by_name = {m.name: m for m in models}
    dashboards = [d for o in bundle.of("liveboard") + bundle.of("answer")
                  if (d := guarded(o, lambda o: tml.parse_content(o, by_name))) is not None]
    if not models:
        issues.append(ParseIssue(severity="error", message="no ThoughtSpot worksheet, model or SQL view found"))
    return ModelExport(tool="thoughtspot", location=location, fingerprint=fingerprint, source_format="tml",
                       models=tuple(models), dashboards=tuple(dashboards), issues=tuple(issues),
                       status="error" if any(i.severity == "error" for i in issues) else "ok")


def export(location: str) -> ModelExport:
    path, fingerprint = materialize(location)
    return _build(str(path), fingerprint, location)
