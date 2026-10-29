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
    IgnoreEntry, Mapping, MappingEntry, MigrationPackage, ModelExport, ParseIssue, Side,
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


def _read_yaml(location: str, name: str) -> dict:
    path, _ = materialize(location)
    f = Path(path) / name
    if not hasattr(_local, "yaml"):
        _local.yaml = YAML(typ="safe")
    return (_local.yaml.load(f.read_text()) or {}) if f.exists() else {}


def _mapping(doc: dict) -> Mapping | None:
    if not doc:
        return None

    def entries(key):
        return tuple(MappingEntry(thoughtspot=str(e["thoughtspot"]), strategy=str(e["strategy"])) for e in doc.get(key) or [])

    return Mapping(models=entries("models"), fields=entries("fields"), visualizations=entries("visualizations"),
                   ignore=tuple(IgnoreEntry(thoughtspot=str(e["thoughtspot"]), reason=str(e.get("reason", "")))
                                for e in doc.get("ignore") or []))


def _assemble(location: str) -> tuple[MigrationPackage, str]:
    manifest = _read_yaml(location, "manifest.yaml")
    exports: dict[str, ModelExport | None] = {}
    issues: list[ParseIssue] = []
    for side in SIDES:
        folder = str((manifest.get(side) or {}).get("folder", side)).strip("/")
        reply = expert(side).call("export_model", {"location": join(location, folder)})
        if "error" in reply:
            issues.append(ParseIssue(severity="error", message=f"{side} expert: {reply['error']}"))
            exports[side] = None
            continue
        exp = ModelExport.model_validate(reply)
        exports[side] = exp
        issues.extend(i.model_copy(update={"message": f"{side}: {i.message}"}) for i in exp.issues)

    def side(name: str) -> Side:
        exp = exports.get(name)
        return Side(models=exp.models, dashboards=exp.dashboards) if exp else Side()

    mapping_name = manifest.get("mapping") or "mapping.yaml"
    pkg = MigrationPackage(
        id=str(manifest.get("package_id") or location.rstrip("/").rpartition("/")[2]), root=location,
        thoughtspot=side("thoughtspot"), strategy=side("strategy"),
        mapping=_mapping(_read_yaml(location, str(mapping_name))),
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
