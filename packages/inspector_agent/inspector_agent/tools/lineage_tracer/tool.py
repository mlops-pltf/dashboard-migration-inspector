"""Tool: trace_lineage."""

from __future__ import annotations

import functools

from strands import tool

from inspector_contract import to_text
from ..package_inventory.loader import load_cached, location_of, package_key
from .resolve import Resolved, resolve_model

SIDES = ("thoughtspot", "strategy")


@functools.lru_cache(maxsize=64)
def _resolved(location: str, key: str, side: str) -> dict[str, Resolved]:
    pkg = load_cached(location)
    models = pkg.thoughtspot.models if side == "thoughtspot" else pkg.strategy.models
    return resolve_model(models[0]) if models else {}


def resolved(package: str, side: str) -> dict[str, Resolved]:
    """All fields of one side, resolved (cached per package version)."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    location = location_of(package)
    return _resolved(location, package_key(location), side)


def trace_lineage_impl(package: str, side: str, field: str) -> dict:
    pkg = load_cached(package)
    model = (pkg.thoughtspot if side == "thoughtspot" else pkg.strategy).models[0]
    f = model.field(field)
    if f is None:
        return {"error": f"no {side} field {field!r}", "fields": [x.name for x in model.fields]}
    r = resolved(package, side)[f.name]
    return {
        "side": side, "field": f.name, "kind": f.kind,
        "as_written": to_text(f.expr), "source": str(f.source) if f.source else None,
        "resolved": to_text(r.expr), "status": r.status,
        "physical_columns": [str(c) for c in r.columns],
        **({"equivalent_columns": [str(c) for c in r.equivalent_columns]}
           if len(r.equivalent_columns) > len(r.columns) else {}),
        "logical_tables_read": list(r.tables),
        "issues": list(r.issues),
    }


@tool
def trace_lineage(package: str, side: str, field: str) -> dict:
    """Trace a field down to warehouse columns: follows table paths, SQL views, Freeform SQL lineage and metric
    references, and resolves aggregation levels to columns. Returns the definition as written, the resolved
    expression, the physical columns (and, for join-key attributes, every equivalent column on both sides of
    the join), the logical tables read, and anything that could not be resolved.

    Args:
        package: Path of the package folder or zip.
        side: "thoughtspot" or "strategy".
        field: Field / attribute / metric name.
    """
    return trace_lineage_impl(package, side, field)
