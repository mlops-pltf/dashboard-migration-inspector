"""A migration package, assembled from the experts.

The Inspector reads only the package's manifest and mapping file itself. Each side folder (thoughtspot/,
strategy/) is exported to IR by its expert agent over A2A (structured skill export_model). Results are cached
per package location; `refresh` re-asks the experts (every validation run starts with a refresh).
"""

from __future__ import annotations

import threading
from pathlib import Path

from ruamel.yaml import YAML

from inspector_contract import (
    IgnoreEntry, Mapping, MappingEntry, MigrationPackage, ModelExport, ParseIssue, Side, SourceRef,
)

from ..expert_client.client import SIDES, expert
from .locations import join, materialize

_local = threading.local()  # ruamel YAML instances are not thread-safe: one per thread
_cache: dict[str, tuple[MigrationPackage, str]] = {}


def location_of(package: str) -> str:
    if str(package).startswith("s3://"):
        return str(package).rstrip("/")
    path, _ = materialize(str(package))
    return str(path)


def _read_yaml(location: str, name: str, issues: list[ParseIssue]) -> dict:
    """An optional package file as a mapping; a malformed one is reported and treated as absent."""
    path, _ = materialize(location)
    f = Path(path) / name
    if not f.exists():
        return {}
    if not hasattr(_local, "yaml"):
        _local.yaml = YAML(typ="safe")
    try:
        doc = _local.yaml.load(f.read_text(encoding="utf-8-sig")) or {}
    except Exception as exc:
        doc = exc
    if isinstance(doc, dict):
        return doc
    issues.append(ParseIssue(severity="error", source=SourceRef(file=name),
                             message=f"{name} is not a YAML mapping ({doc if isinstance(doc, Exception) else type(doc).__name__}); ignored"))
    return {}


def _mapping(doc: dict, name: str, issues: list[ParseIssue]) -> Mapping | None:
    if not doc:
        return None

    def items(key):
        value = doc.get(key) or []
        good = [e for e in value if isinstance(e, dict) and e.get("thoughtspot")] if isinstance(value, list) else []
        if len(good) != (len(value) if isinstance(value, list) else 1):
            issues.append(ParseIssue(severity="error", source=SourceRef(file=name, path=key),
                                     message=f"{name}: {key} entries need thoughtspot and strategy keys; malformed entries ignored"))
        return good

    def entries(key):
        return tuple(MappingEntry(thoughtspot=str(e["thoughtspot"]), strategy=str(e.get("strategy") or ""))
                     for e in items(key))

    return Mapping(models=entries("models"), fields=entries("fields"), visualizations=entries("visualizations"),
                   ignore=tuple(IgnoreEntry(thoughtspot=str(e["thoughtspot"]), reason=str(e.get("reason", "")))
                                for e in items("ignore")))


def _assemble(location: str) -> tuple[MigrationPackage, str]:
    issues: list[ParseIssue] = []
    manifest = _read_yaml(location, "manifest.yaml", issues)
    exports: dict[str, ModelExport | None] = {}
    for side in SIDES:
        entry = manifest.get(side)
        folder = str((entry if isinstance(entry, dict) else {}).get("folder") or side).strip("/")
        try:
            reply = expert(side).call("export_model", {"location": join(location, folder)})
        except Exception as exc:  # the expert is down: the package cannot be read, report it instead of failing the run
            reply = {"error": f"unreachable: {type(exc).__name__}: {exc}"}
        if "error" in reply:
            issues.append(ParseIssue(severity="error", message=f"{side} expert: {reply['error']}"))
            exports[side] = None
            continue
        exp = ModelExport.model_validate(reply)
        exports[side] = exp
        issues.extend(i.model_copy(update={"message": f"{side}: " + (f"{i.source}: " if i.source else "") + i.message})
                      for i in exp.issues)

    def side(name: str) -> Side:
        exp = exports.get(name)
        return Side(models=exp.models, dashboards=exp.dashboards) if exp else Side()

    mapping_name = manifest.get("mapping") or "mapping.yaml"
    pkg = MigrationPackage(
        id=str(manifest.get("package_id") or location.rstrip("/").rpartition("/")[2]), root=location,
        thoughtspot=side("thoughtspot"), strategy=side("strategy"),
        mapping=_mapping(_read_yaml(location, str(mapping_name), issues), str(mapping_name), issues),
        strategy_format=exports["strategy"].source_format if exports.get("strategy") else None,
        issues=tuple(issues))
    key = location + "|" + "|".join(e.fingerprint for e in exports.values() if e)
    return pkg, key


def load_cached(package: str, refresh: bool = False) -> MigrationPackage:
    location = location_of(package)
    if refresh or location not in _cache:
        _cache[location] = _assemble(location)
    return _cache[location][0]


def package_key(package: str) -> str:
    """Identifies this version of the package (location + the experts' fingerprints), for caches."""
    location = location_of(package)
    if location not in _cache:
        load_cached(location)
    return _cache[location][1]
