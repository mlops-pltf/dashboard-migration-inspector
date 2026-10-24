"""Strategy joins made explicit for the IR.

Mosaic lookup models have no declared joins: two tables join through an attribute whose key form lives on both
(the fact placement joins to the attribute's lookup table). The join type comes from the model's join
semantics: "[Joins].[Preserve all final pass result elements]" set to an outer-join option keeps unmatched fact
rows (LEFT); otherwise rows without a lookup match drop out (INNER). Assumption A2 until confirmed on a real export.
Freeform SQL joins stay inside the table's SQL lineage (applied to every query).
"""

from __future__ import annotations

from inspector_contract import Col, Join, SemanticModel

PRESERVE_FINAL_PASS = "preserve all final pass result elements"


def join_type(model: SemanticModel) -> str:
    if model.kind == "ossie":
        return "UNKNOWN"
    props = {str(k).lower(): str(v).lower() for k, v in (model.settings.get("advancedProperties") or {}).items()}
    return "LEFT" if any(PRESERVE_FINAL_PASS in k and "outer" in v for k, v in props.items()) else "INNER"


def derive_joins(model: SemanticModel) -> tuple[Join, ...]:
    if model.kind == "ossie":
        return model.joins
    jtype = join_type(model)
    edges: dict[tuple[str, str], Join] = {}
    for f in model.fields:
        if f.kind != "ATTRIBUTE" or len({p.table for p in f.placements}) < 2 or not isinstance(f.expr, Col):
            continue
        lookup = f.expr
        for p in f.placements:
            if not isinstance(p.expr, Col) or p.table == lookup.table:
                continue
            key = (p.table, lookup.table)
            pair = (p.expr.column, lookup.column)
            if key in edges:
                if pair not in edges[key].keys:
                    edges[key] = edges[key].model_copy(update={"keys": edges[key].keys + (pair,)})
                continue
            edges[key] = Join(id=f"{p.table} -> {lookup.table} via {f.name}", left=p.table, right=lookup.table,
                              type=jtype, keys=(pair,), cardinality="N:1", origin="derived", applied="when_needed",
                              source=f.source)
    return tuple(edges.values())
