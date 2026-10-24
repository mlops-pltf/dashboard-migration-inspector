"""Tools: match_fields, find_counterparts."""

from __future__ import annotations

from difflib import SequenceMatcher

from strands import tool

from ..lineage_tracer.tool import resolved
from ..package_inventory.loader import load_cached
from .matching import match_fields as _match, norm, pick_models


def ignored_fields(pkg, ts_name: str) -> dict[str, str]:
    out = {}
    if pkg.mapping:
        for e in pkg.mapping.ignore:
            model, _, name = e.thoughtspot.rpartition("::")
            if not model or model == ts_name:
                out[name] = e.reason
    return out


def matches(package: str):
    pkg = load_cached(package)
    ts, st = pick_models(pkg)
    return _match(pkg, ts, st, resolved(package, "thoughtspot"), resolved(package, "strategy"),
                  ignored_fields(pkg, ts.name))


def match_fields_impl(package: str) -> dict:
    pkg = load_cached(package)
    ts, st = pick_models(pkg)
    ms = matches(package)
    return {
        "thoughtspot_model": ts.name, "strategy_model": st.name,
        "pairs": [{"thoughtspot": m.ts, "strategy": m.st, "basis": m.basis, "score": m.score}
                  for m in ms if m.ts and m.st],
        "ambiguous": [{"thoughtspot": m.ts, "candidates": list(m.candidates)} for m in ms if m.basis == "ambiguous"],
        "only_in_thoughtspot": [m.ts for m in ms if m.ts and not m.st and m.basis != "ambiguous"],
        "only_in_strategy": [m.st for m in ms if m.st and not m.ts],
        "ignored_by_mapping": ignored_fields(pkg, ts.name),
    }


def find_counterparts_impl(package: str, thoughtspot_field: str, top: int = 5) -> dict:
    ts_res, st_res = resolved(package, "thoughtspot"), resolved(package, "strategy")
    if thoughtspot_field not in ts_res:
        return {"error": f"no ThoughtSpot field {thoughtspot_field!r}"}
    a = ts_res[thoughtspot_field]
    rows = []
    for name, b in st_res.items():
        cols_a, cols_b = set(a.columns), set(b.equivalent_columns or b.columns)
        overlap = len(cols_a & cols_b) / len(cols_a | cols_b) if cols_a | cols_b else 0.0
        name_sim = SequenceMatcher(None, norm(thoughtspot_field), norm(name)).ratio()
        rows.append({"strategy": name, "kind": b.kind, "same_kind": a.kind == b.kind,
                     "shared_columns": round(overlap, 2), "name_similarity": round(name_sim, 2),
                     "score": round((0.6 * overlap + 0.4 * name_sim) if a.kind == b.kind else 0.0, 3)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"thoughtspot": thoughtspot_field, "kind": a.kind, "columns": [str(c) for c in a.columns],
            "candidates": rows[:top]}


@tool
def match_fields(package: str) -> dict:
    """Pair every ThoughtSpot field with its Strategy counterpart: from mapping.yaml first, then identical names
    (and synonyms), then inference from shared warehouse columns and name similarity. Close calls are returned as
    ambiguous with their candidates; leftovers on either side are listed.

    Args:
        package: Path of the package folder or zip.
    """
    return match_fields_impl(package)


@tool
def find_counterparts(package: str, thoughtspot_field: str, top: int = 5) -> dict:
    """Rank Strategy objects as possible counterparts of one ThoughtSpot field, with the evidence for each:
    same kind, share of warehouse columns in common, name similarity.

    Args:
        package: Path of the package folder or zip.
        thoughtspot_field: The ThoughtSpot field name.
        top: How many candidates to return.
    """
    return find_counterparts_impl(package, thoughtspot_field, top)
