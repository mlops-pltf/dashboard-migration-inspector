"""Tool: compare_fields."""

from __future__ import annotations

from strands import tool

from inspector_contract import PCol, to_text
from ..lineage_tracer.tool import resolved
from ..package_inventory.loader import load_cached
from .canonical import canonical
from .classify import classify


def compare_fields_impl(package: str, thoughtspot_field: str, strategy_field: str) -> dict:
    ts_res, st_res = resolved(package, "thoughtspot"), resolved(package, "strategy")
    if thoughtspot_field not in ts_res:
        return {"error": f"no ThoughtSpot field {thoughtspot_field!r}"}
    if strategy_field not in st_res:
        return {"error": f"no Strategy field {strategy_field!r}"}
    t, s = ts_res[thoughtspot_field], st_res[strategy_field]
    lossy = list(load_cached(package).strategy.models[0].lossy)
    out = {"thoughtspot": thoughtspot_field, "strategy": strategy_field, "kinds": [t.kind, s.kind]}
    ct, cs = canonical(t.expr), canonical(s.expr)
    out["thoughtspot_canonical"], out["strategy_canonical"] = to_text(ct), to_text(cs)

    if t.kind == "ATTRIBUTE" and s.kind == "ATTRIBUTE" and isinstance(t.expr, PCol) and isinstance(s.expr, PCol):
        options = set(s.equivalent_columns or s.columns)
        same = t.expr.column in options or bool(set(t.equivalent_columns) & options)
        out["identical"] = same
        out["proof"] = "reads the same warehouse column (directly or via an equivalent join key)" if same else None
        if not same:
            out["difference"] = {"check": "LN-01", "status": "FAIL", "explanation": "reads a different column"}
        return out

    if t.kind != s.kind:
        out["identical"] = False
        out["difference"] = {"check": "FX-01", "status": "FAIL", "explanation": "attribute on one side, measure on the other"}
        return out
    if ct == cs:
        out["identical"] = True
        out["proof"] = "canonical forms are identical"
        return out
    check, status, why = classify(ct, cs, tuple(lossy))
    out["identical"] = False
    out["difference"] = {"check": check, "status": status, "explanation": why}
    if lossy:
        out["strategy_input_loses"] = lossy
    if status == "UNVERIFIED":
        out["note"] = ("Rules cannot prove or disprove equality. Reason about the semantics yourself and/or request "
                       "a data probe; a PASS needs data evidence.")
    return out


@tool
def compare_fields(package: str, thoughtspot_field: str, strategy_field: str) -> dict:
    """Compare a ThoughtSpot field with a Strategy object after resolving both to warehouse columns and putting
    them in canonical form. "identical": true is a proof of equal meaning. Otherwise "difference" says what kind
    of difference the rules see (aggregate function AG-01, level AG-02, aggregation order AG-03, condition FL-02,
    division by zero FX-03, different column LN-01) or that equality cannot be decided by rules (FX-01
    UNVERIFIED), in which case you must reason about it and/or ask for data.

    Args:
        package: Path of the package folder or zip.
        thoughtspot_field: ThoughtSpot field name.
        strategy_field: Strategy attribute / metric name.
    """
    return compare_fields_impl(package, thoughtspot_field, strategy_field)
