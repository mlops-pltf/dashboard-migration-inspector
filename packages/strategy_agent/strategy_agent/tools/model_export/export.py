"""Strategy folder -> ModelExport (IR). Detects the model format (YAML export, REST JSON, Ossie YAML), reads
dashboards and supplied SQL, and makes the join semantics explicit."""

from __future__ import annotations

import functools
from pathlib import Path

from inspector_contract import ModelExport, ParseIssue, SourceRef

from ..common.locations import materialize
from ..common.yamlio import load_json, load_yaml
from ..strategy_reader import dashboard, mosaic, ossie
from .join_semantics import derive_joins, join_type


def _detect(root: Path) -> list[tuple[str, Path]]:
    found = []
    for p in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")):
        try:
            doc = load_yaml(p)
        except Exception:
            continue
        if isinstance(doc, dict) and "semantic_model" in doc:
            found.append(("ossie_yaml", p))
        elif isinstance(doc, dict) and isinstance(doc.get("model"), list):
            found.append(("yaml_export", p))
    found.extend(("rest_json", p.parent) for p in sorted(root.rglob("dataModel.json")))
    return found


LOADERS = {"yaml_export": mosaic.load_yaml_export, "rest_json": mosaic.load_rest_json, "ossie_yaml": ossie.load}


@functools.lru_cache(maxsize=32)
def _build(path: str, fingerprint: str, location: str) -> ModelExport:
    root = Path(path)
    issues: list[ParseIssue] = []
    models, fmt = [], None
    for kind, source in _detect(root):
        model, more = LOADERS[kind](source, root)
        jtype = join_type(model)
        settings = dict(model.settings) | {"join_semantics": {
            "LEFT": "derived joins keep unmatched rows (preserve final pass result elements = outer join)",
            "INNER": "derived joins drop rows without a lookup match (default)",
            "UNKNOWN": "the input format does not carry join types"}[jtype]}
        models.append(model.model_copy(update={"joins": derive_joins(model), "settings": settings}))
        issues.extend(more)
        fmt = fmt or kind
    if not models:
        issues.append(ParseIssue(severity="error", message="no Strategy model found (YAML export, REST JSON or Ossie YAML)"))
    dashboards = []
    for p in sorted((root / "dashboards").glob("*.json")) if (root / "dashboards").exists() else []:
        try:
            dashboards.append(dashboard.load(p, root))
        except Exception as exc:
            issues.append(ParseIssue(severity="error", message=f"dashboard: {exc}", source=SourceRef(file=p.name)))
    sql = {}
    for p in sorted((root / "sql").glob("*.json")) if (root / "sql").exists() else []:
        doc = load_json(p)
        if isinstance(doc, dict) and doc.get("sqlStatement"):
            sql[p.stem] = str(doc["sqlStatement"])
    return ModelExport(tool="strategy", location=location, fingerprint=fingerprint, source_format=fmt,
                       models=tuple(models), dashboards=tuple(dashboards), sql=sql, issues=tuple(issues),
                       status="error" if any(i.severity == "error" for i in issues) else "ok")


def export(location: str) -> ModelExport:
    path, fingerprint = materialize(location)
    return _build(str(path), fingerprint, location)
