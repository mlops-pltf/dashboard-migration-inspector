"""ThoughtSpot expert: formulas, search queries, SQL lineage, IR export of every synthetic package, A2A skills."""

from pathlib import Path

import httpx
import pytest

from inspector_contract import ModelExport, Opaque, to_text
from inspector_contract.a2a import A2AClient
from thoughtspot_agent.thoughtspot_agent import TOOLS, app, build_agent
from thoughtspot_agent.tools.model_export.export import export
from thoughtspot_agent.tools.sql_analyzer.lineage import analyze
from thoughtspot_agent.tools.thoughtspot_reader import formula, search

PACKAGES = Path(__file__).resolve().parents[3] / "synthetic" / "packages"
IDS = sorted(p.name for p in PACKAGES.iterdir()) if PACKAGES.exists() else []
needs_synthetic = pytest.mark.skipif(not IDS, reason="run `uv run inspector-synth generate` first")


@pytest.mark.parametrize("text, expected", [
    ("unique count ( [T_1::Order ID] )", "COUNT(DISTINCT [T_1::Order ID])"),
    ("sum ( [T::a] ) / unique count ( [T::b] )", "SUM([T::a]) / COUNT(DISTINCT [T::b])"),
    ("safe_divide ( sum ( [T::a] ) , sum ( [T::b] ) )", "CASE WHEN SUM([T::b]) = 0 THEN 0 ELSE SUM([T::a]) / SUM([T::b]) END"),
    ("sum ( [T::a] - ifnull ( [T::b] , 0 ) )", "SUM([T::a] - COALESCE([T::b], 0))"),
    ("group_aggregate ( sum ( [T::a] ) , { [D::Region] } , query_filters ( ) )", "SUM([T::a]) @fixed{D::Region}"),
    ("group_aggregate ( sum ( [Revenue] ) , query_groups ( ) + { [Region] } , { } )",
     "SUM([Revenue]) @include{Region} ignoring filters"),
    ("group_aggregate ( sum ( [Revenue] ) , query_groups ( ) - { [Region] } , query_filters ( ) )",
     "SUM([Revenue]) @exclude{Region}"),
    ("group_sum ( [Revenue] , [Region] , [Year] )", "SUM([Revenue]) @fixed{Region, Year}"),
    ("sum_if ( [Region] = 'west' , [Revenue] )", "SUM([Revenue]) FILTER (WHERE [Region] = 'west')"),
    ("if ( [x] > 0 and not [y] = 1 ) then 'a' else if [x] < 0 then 'b' else 'c'",
     "CASE WHEN [x] > 0 AND NOT ([y] = 1) THEN 'a' WHEN [x] < 0 THEN 'b' ELSE 'c' END"),
    ("isnull ( [x] )", "[x] IS NULL"),
])
def test_formulas(text, expected):
    assert to_text(formula.parse(text)) == expected


def test_bad_formula_is_opaque():
    assert isinstance(formula.parse("sum ( [x] ) ) oops"), Opaque)


def test_search_query():
    kinds = {"Revenue": "MEASURE", "Region": "ATTRIBUTE", "Order Date": "ATTRIBUTE"}
    q = search.parse("[Region] [Revenue] [Order Date].monthly [Region] = 'West' average [Revenue] "
                     "top 10 sort by [Revenue] descending last 30 days", kinds.get)
    assert [d.field for d in q.dimensions] == ["Region", "Order Date"] and q.dimensions[1].bucket == "monthly"
    assert [(m.field, m.aggregation) for m in q.measures] == [("Revenue", None), ("Revenue", "AVG")]
    assert q.top_n == 10 and q.sort == (("Revenue", "DESC"),) and q.unparsed == ("last 30 days",)


def test_sql_lineage():
    lin = analyze("SELECT o.id, c.region AS r FROM retail.orders o LEFT JOIN retail.customers c ON c.id = o.cid "
                  "WHERE o.status <> 'x'")
    assert to_text(lin.outputs["r"]) == "retail.customers.region" and lin.joins[0].type == "LEFT"
    assert [to_text(w) for w in lin.where] == ["retail.orders.status != 'x'"]


@needs_synthetic
@pytest.mark.parametrize("sid", IDS)
def test_every_package_exports(sid):
    exp = export(str(PACKAGES / sid / "thoughtspot"))
    assert exp.status == "ok" and exp.models and exp.dashboards and exp.sql, exp.issues
    assert ModelExport.model_validate_json(exp.model_dump_json()) == exp
    model = exp.models[0]
    assert all(j.applied == "when_needed" for j in model.joins)  # progressive joins
    assert all(f.source and f.source.line for f in model.fields)


@needs_synthetic
def test_semantics_are_explicit_in_ir():
    m = export(str(PACKAGES / "S01" / "thoughtspot")).models[0]
    assert "CASE WHEN" in to_text(m.field("Discount Rate").expr)
    assert {j.type for j in m.joins} == {"LEFT"}
    s12 = export(str(PACKAGES / "S12" / "thoughtspot")).models[0]
    assert next(j for j in s12.joins if j.right == "DIM_CUSTOMER_1").type == "INNER"


@needs_synthetic
def test_a2a_structured_skills_and_card():
    client = A2AClient("http://ts/", transport=httpx.ASGITransport(app=app(conversation=False)))
    card = client.card()
    assert card.name == "thoughtspot_agent" and {s.id for s in card.skills} >= {"export_model", "analyze_sql", "ask"}
    exp = ModelExport.model_validate(client.call("export_model", {"location": str(PACKAGES / "S01" / "thoughtspot")}))
    assert exp.tool == "thoughtspot" and exp.models[0].name == "Sales Orders"
    assert "error" in client.call("export_model", {"location": "/nope"})
    assert "error" in client.call("no_such_skill", {})


def test_agent_builds():
    assert {t.tool_name for t in TOOLS} == set(build_agent().tool_names)


@needs_synthetic
def test_exports_are_thread_safe():
    """Concurrent requests (two validations at once) must not corrupt each other's YAML parsing."""
    from concurrent.futures import ThreadPoolExecutor

    from thoughtspot_agent.tools.model_export.tool import summarize_export_impl

    location = str(PACKAGES / "S04" / "thoughtspot")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: summarize_export_impl(location), range(16)))
    assert all(r["models"] and not any(i["severity"] == "error" for i in r["issues"]) for r in results)
    assert len({r["fingerprint"] for r in results}) == 1
