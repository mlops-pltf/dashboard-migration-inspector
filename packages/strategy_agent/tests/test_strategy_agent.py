"""Strategy expert: expressions, IR export (formats, derived joins, Freeform SQL lineage), A2A skills."""

from pathlib import Path

import httpx
import pytest

from inspector_contract import Agg, ModelExport, to_text
from inspector_contract.a2a import A2AClient
from strategy_agent.strategy_agent import TOOLS, app, build_agent
from strategy_agent.tools.model_export.export import export
from strategy_agent.tools.strategy_reader import expression

PACKAGES = Path(__file__).resolve().parents[3] / "synthetic" / "packages"
IDS = sorted(p.name for p in PACKAGES.iterdir()) if PACKAGES.exists() else []
needs_synthetic = pytest.mark.skipif(not IDS, reason="run `uv run inspector-synth generate` first")


@pytest.mark.parametrize("text, expected", [
    ("[Revenue] / [Order Count]", "[Revenue] / [Order Count]"),
    ("If(([Revenue] = 0), 0, ([Discount] / [Revenue]))", "CASE WHEN [Revenue] = 0 THEN 0 ELSE [Discount] / [Revenue] END"),
    ("Sum<UseLookupForAttributes=False>([Year Duration]){~+}", "SUM([Year Duration])"),
    ("Count<Distinct=True, UseLookupForAttributes=False>([Order ID])", "COUNT(DISTINCT [Order ID])"),
    ('ApplySimple("#0 - COALESCE(#1, 0)", order_amount, discount_amount)', "[order_amount] - COALESCE([discount_amount], 0)"),
    ("[Revenue] - NullToZero([Discount])", "[Revenue] - COALESCE([Discount], 0)"),
    ("Sum(Revenue){~+, [Store Region]}", "SUM([Revenue]) @include{Store Region}"),
    ("Sum({On-Time Flights}){Year}", "SUM([On-Time Flights]) @fixed{Year}"),
    ("Sum(x) {~+, !Year}", "SUM([x]) @exclude{Year}"),
    ("Sum(Revenue) <[Completed Orders]>", "SUM([Revenue]) FILTER (WHERE [Completed Orders])"),
    ("Case(([a] > 1), 'x', ([a] > 0), 'y', 'z')", "CASE WHEN [a] > 1 THEN 'x' WHEN [a] > 0 THEN 'y' ELSE 'z' END"),
])
def test_expressions(text, expected):
    assert to_text(expression.parse(text)) == expected


def test_report_level():
    e = expression.parse("Sum([x]){~+}")
    assert isinstance(e, Agg) and e.level.kind == "report"


@needs_synthetic
@pytest.mark.parametrize("sid", IDS)
def test_every_package_exports(sid):
    exp = export(str(PACKAGES / sid / "strategy"))
    assert not [i for i in exp.issues if i.severity == "error"], exp.issues
    assert exp.models and exp.dashboards
    assert not [i for i in exp.issues if "not in the model export" in i.message]  # .mstr ids join the model
    assert ModelExport.model_validate_json(exp.model_dump_json()) == exp


@needs_synthetic
def test_formats_and_join_semantics():
    s01 = export(str(PACKAGES / "S01" / "strategy"))
    assert s01.source_format == "yaml_export"
    joins = s01.models[0].joins
    assert {(j.left, j.right, j.type, j.origin, j.applied) for j in joins} == {
        ("fact_orders", "dim_customer", "LEFT", "derived", "when_needed"),
        ("fact_orders", "dim_store", "LEFT", "derived", "when_needed"),
        ("fact_orders", "dim_date", "LEFT", "derived", "when_needed")}
    assert next(j for j in joins if j.right == "dim_date").keys == (("order_date", "date_key"),)
    assert "outer" in s01.models[0].settings["join_semantics"]
    assert export(str(PACKAGES / "S02" / "strategy")).source_format == "rest_json"
    s04 = export(str(PACKAGES / "S04" / "strategy")).models[0]
    assert s04.joins == () and s04.tables[0].relation.kind == "sql"  # joins live inside the Freeform SQL lineage
    assert len(s04.tables[0].relation.lineage.joins) == 5
    s18 = export(str(PACKAGES / "S18" / "strategy"))
    assert s18.source_format == "ossie_yaml" and {j.type for j in s18.models[0].joins} == {"UNKNOWN"}
    assert "metric levels" in s18.models[0].lossy


@needs_synthetic
def test_a2a_structured_skills_and_card():
    client = A2AClient("http://st/", transport=httpx.ASGITransport(app=app(conversation=False)))
    assert client.card().name == "strategy_agent"
    exp = ModelExport.model_validate(client.call("export_model", {"location": str(PACKAGES / "S13" / "strategy")}))
    assert exp.models[0].tables[0].name == "Returns Analysis SQL"
    out = client.call("analyze_sql", {"sql": "SELECT a.x FROM retail.t a"})
    assert out["outputs"] == {"x": "retail.t.x"}


def test_agent_builds():
    assert {t.tool_name for t in TOOLS} == set(build_agent().tool_names)


@needs_synthetic
def test_exports_are_thread_safe():
    """Concurrent requests (two validations at once) must not corrupt each other's YAML parsing."""
    from concurrent.futures import ThreadPoolExecutor

    from strategy_agent.tools.model_export.tool import summarize_export_impl

    location = str(PACKAGES / "S04" / "strategy")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: summarize_export_impl(location), range(16)))
    assert all(r["models"] and not any(i["severity"] == "error" for i in r["issues"]) for r in results)
    assert len({r["fingerprint"] for r in results}) == 1
