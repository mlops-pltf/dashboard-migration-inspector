"""Model-level filters on each side, in canonical form, so the same condition compares equal.

ThoughtSpot: worksheet/model filters. Strategy: model filters plus the WHERE clause of every SQL-backed
table (Freeform SQL, a filtered fact table, an Ossie custom-SQL dataset). Both apply to every query.
"""

from __future__ import annotations

from dataclasses import dataclass

from inspector_contract import SemanticModel, to_text
from ..definition_comparer.canonical import canonical
from ..lineage_tracer.resolve import resolve_expr


@dataclass(frozen=True)
class ModelFilterInfo:
    condition: str  # canonical text over warehouse columns
    origin: str  # "worksheet filter" | "model filter" | "<table> WHERE"
    source: str | None


def thoughtspot_filters(model: SemanticModel) -> list[ModelFilterInfo]:
    out = []
    for f in model.filters:
        expr, _ = resolve_expr(model, f.expr)
        out.append(ModelFilterInfo(to_text(canonical(expr)), f"{model.kind} filter",
                                   str(f.source) if f.source else None))
    return out


def strategy_filters(model: SemanticModel) -> list[ModelFilterInfo]:
    out = []
    for f in model.filters:
        expr, _ = resolve_expr(model, f.expr)
        out.append(ModelFilterInfo(to_text(canonical(expr)), "model filter", str(f.source) if f.source else None))
    for t in model.tables:
        lin = t.relation.lineage
        if t.relation.kind == "sql" and lin is not None:
            out.extend(ModelFilterInfo(to_text(canonical(w)), f"{t.name} WHERE", str(t.source) if t.source else None)
                       for w in lin.where)
    return out


def compare(ts: SemanticModel, st: SemanticModel) -> dict:
    a, b = thoughtspot_filters(ts), strategy_filters(st)
    ta, tb = {x.condition for x in a}, {x.condition for x in b}
    return {
        "matched": [x for x in a if x.condition in tb],
        "missing_in_strategy": [x for x in a if x.condition not in tb],
        "only_in_strategy": [x for x in b if x.condition not in ta],
        "strategy_by_condition": {x.condition: x for x in b},
    }
