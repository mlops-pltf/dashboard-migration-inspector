"""The query compiler reproduces ThoughtSpot's own SQL: for every liveboard visualization in every package, the
SQL compiled from the ThoughtSpot IR returns the same numbers as the SQL ThoughtSpot generated (supplied in the
package)."""

from pathlib import Path

import pytest

from inspector_contract import FilterSpec
from inspector_agent.tools.counterpart_finder.matching import pick_models
from inspector_agent.tools.data_probe.client import call
from inspector_agent.tools.package_inventory.loader import load_cached
from inspector_agent.tools.query_compiler.compile import compile_query

from conftest import IDS, PACKAGES, needs_synthetic, needs_warehouse, pkg


def _cases():
    for sid in IDS:
        for f in sorted((PACKAGES / sid / "thoughtspot" / "sql").glob("*/*.sql")):
            yield pytest.param(sid, f, id=f"{sid}-{f.stem}")


def _viz(pkg_, board: str, name: str):
    for d in pkg_.thoughtspot.dashboards:
        for v in d.visualizations:
            if v.name == name and (d.name == board or board == name):
                return d, v
    return None, None


@needs_synthetic
@needs_warehouse
@pytest.mark.parametrize("sid, sql_file", list(_cases()))
def test_compiled_thoughtspot_sql_matches_generated_sql(sid, sql_file: Path):
    p = load_cached(pkg(sid))
    ts, _ = pick_models(p)
    d, v = _viz(p, sql_file.parent.name, sql_file.stem)
    assert v is not None, f"no visualization for {sql_file}"
    if v.query.top_n:
        pytest.skip("top-N visualization")
    q = v.query
    filters = list(q.filters) + [FilterSpec(field=f.field, op=f.op, values=f.values)
                                 for f in d.filters if not f.scope or v.id in f.scope]
    c = compile_query(ts, [m.field for m in q.measures], list(q.dimensions), filters)
    assert not c.unsupported, c.unsupported
    cols = [f'"{u.field}" AS d{i}' for i, u in enumerate(q.dimensions)] + \
           [f'"{u.field}" AS m{i}' for i, u in enumerate(q.measures)]
    generated = f"SELECT {', '.join(cols)} FROM ({sql_file.read_text().strip().rstrip(';')}) AS g"
    r = call("compare_queries", {"left_sql": generated, "right_sql": c.sql,
                                 "keys": [f"d{i}" for i in range(len(q.dimensions))],
                                 "measures": [f"m{i}" for i in range(len(q.measures))],
                                 "abs_tolerance": 0.01, "rel_tolerance": 1e-9})
    assert r.get("status") == "MATCH", (r, c.sql)


def test_division_by_zero_is_null_and_safe_divide_explicit():
    from inspector_contract import Agg, BinOp, Col, Field, LogicalTable, PhysicalTable, Relation, SemanticModel

    t = LogicalTable(id="F", name="F", relation=Relation(kind="physical",
                                                         table=PhysicalTable(namespace="retail", table="f")))
    a, b = Agg(fn="SUM", arg=Col(table="F", column="a")), Agg(fn="SUM", arg=Col(table="F", column="b"))
    m = SemanticModel(id="m", name="m", tool="strategy", kind="x", tables=(t,), fields=(
        Field(id="r", name="Ratio", kind="MEASURE", expr=BinOp(op="/", left=a, right=b), object_type="x"),))
    c = compile_query(m, ["Ratio"])
    assert "NULLIF" in c.sql and "DOUBLE PRECISION" in c.sql and not c.unsupported
    assert compile_query(m, ["Nope"]).unsupported
