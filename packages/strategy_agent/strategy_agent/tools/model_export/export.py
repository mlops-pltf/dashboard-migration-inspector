"""Strategy folder -> ModelExport (IR). Reads the model YAML export, the dashboards (binary .mstr) and makes the
join semantics explicit."""

from __future__ import annotations

import functools
from pathlib import Path

from inspector_contract import ModelExport, ParseIssue, SemanticModel, SourceRef

from ..common.locations import materialize, package_files
from ..common.yamlio import load_yaml
from ..mstr_reader import to_ir as mstr_dashboard
from ..strategy_reader import mosaic
from .join_semantics import derive_joins, join_type


def _detect(root: Path) -> list[tuple[str, Path]]:
    found = []
    for p in package_files(root, "*.yaml") + package_files(root, "*.yml"):
        try:
            doc = load_yaml(p)
        except Exception:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("model"), list):
            found.append(("yaml_export", p))
    return found


LOADERS = {"yaml_export": mosaic.load_yaml_export}


def _rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)) if p.is_relative_to(root) else p.name


def _sanity(model: SemanticModel, source: str) -> list[ParseIssue]:
    """A model that loaded but is empty is a misread export, not an empty project: say so instead of
    letting every ThoughtSpot field look unmigrated."""
    missing = [what for what, items in (("tables", model.tables), ("attributes or metrics", model.fields)) if not items]
    return [ParseIssue(severity="error", source=SourceRef(file=source),
                       message=f"model {model.name!r} has no {' and no '.join(missing)}: unexpected file shape?")] if missing else []


@functools.lru_cache(maxsize=32)
def _build(path: str, fingerprint: str, location: str) -> ModelExport:
    root = Path(path)
    issues: list[ParseIssue] = []
    models, fmt = [], None
    for kind, source in _detect(root):
        try:
            model, more = LOADERS[kind](source, root)
        except Exception as exc:  # one unreadable model file must not hide the rest of the package
            issues.append(ParseIssue(severity="error", source=SourceRef(file=_rel(source, root)),
                                     message=f"could not read {kind.replace('_', ' ')}: {type(exc).__name__}: {exc}"))
            continue
        more = list(more) + _sanity(model, _rel(source, root))
        jtype = join_type(model)
        settings = dict(model.settings) | {"join_semantics": {
            "LEFT": "derived joins keep unmatched rows (preserve final pass result elements = outer join)",
            "INNER": "derived joins drop rows without a lookup match (default)"}[jtype]}
        models.append(model.model_copy(update={"joins": derive_joins(model), "settings": settings}))
        issues.extend(more)
        fmt = fmt or kind
    if not models:
        issues.append(ParseIssue(severity="error", message="no Strategy model YAML export found"))
    dashboards = []
    for p in package_files(root, "*.mstr"):
        try:  # the real input: binary .mstr, linked to the model export by object id
            dash, more = mstr_dashboard.load(p, tuple(models), root)
            dashboards.append(dash)
            issues.extend(more)
        except Exception as exc:
            issues.append(ParseIssue(severity="error", message=f"dashboard could not be read: {type(exc).__name__}: {exc}",
                                     source=SourceRef(file=_rel(p, root))))
    issues = list(dict.fromkeys(issues))  # the same object used by many visualizations is reported once
    return ModelExport(tool="strategy", location=location, fingerprint=fingerprint, source_format=fmt,
                       models=tuple(models), dashboards=tuple(dashboards), issues=tuple(issues),
                       status="error" if any(i.severity == "error" for i in issues) else "ok")


def export(location: str) -> ModelExport:
    path, fingerprint = materialize(location)
    return _build(str(path), fingerprint, location)
