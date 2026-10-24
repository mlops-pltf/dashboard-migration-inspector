"""Each Inspector tool on the synthetic packages (experts reached over A2A): correct, JSON-serialisable,
usable without the agent."""

import json

import pytest

from conftest import IDS, needs_synthetic, needs_warehouse, pkg
from inspector_agent.tools.counterpart_finder.tool import find_counterparts_impl, match_fields_impl
from inspector_agent.tools.data_probe.tool import request_data_probe_impl
from inspector_agent.tools.definition_comparer.tool import compare_fields_impl
from inspector_agent.tools.expert_client.tool import analyze_sql_impl
from inspector_agent.tools.filter_inspector.tool import compare_filters_impl
from inspector_agent.tools.join_analyzer.tool import analyze_row_multiplication_impl, compare_joins_impl, get_join_graph_impl
from inspector_agent.tools.lineage_tracer.tool import resolved, trace_lineage_impl
from inspector_agent.tools.model_browser.tool import get_object_impl, list_fields_impl
from inspector_agent.tools.package_inventory.tool import inspect_package_impl
from inspector_agent.tools.rule_sweep.tool import run_rule_sweep_impl

pytestmark = needs_synthetic


@pytest.mark.parametrize("sid", IDS)
def test_every_package_inventories_and_resolves(sid):
    inv = inspect_package_impl(pkg(sid))
    json.dumps(inv)
    assert not [i for i in inv["parse_issues"] if i["severity"] == "error"]
    assert inv["thoughtspot"]["models"] and inv["strategy"]["models"]
    for side in ("thoughtspot", "strategy"):
        for name, r in resolved(pkg(sid), side).items():
            if (sid, side, name) == ("S18", "strategy", "Net Revenue"):  # Ossie drops Apply* expressions
                assert r.status == "partial"
                continue
            assert r.status == "exact" and r.columns, (sid, side, name, r.issues)


def test_inventory_shows_format_losses_and_joins():
    inv = inspect_package_impl(pkg("S18"))
    assert inv["strategy_format"] == "ossie_yaml"
    assert "metric levels" in inv["strategy"]["models"][0]["input_format_loses"]
    assert any("UNKNOWN" in j for j in inv["strategy"]["models"][0]["joins"])


def test_model_browser():
    fields = list_fields_impl(pkg("S01"), "thoughtspot")["fields"]
    assert any(f["name"] == "Discount Rate" and "CASE WHEN" in f["definition"] for f in fields)
    assert get_object_impl(pkg("S01"), "thoughtspot", "Sales Orders")["joins"][0]["applied"] == "when_needed"
    assert "JOIN analytics.retail.fact_order_lines" in get_object_impl(pkg("S04"), "strategy", "Sales Orders SQL")["sql"]
    lb = get_object_impl(pkg("S15"), "thoughtspot", "Sales Overview")
    assert any(v["name"] == "Top Customers" and v["top_n"] == 10 for v in lb["visualizations"])
    assert get_object_impl(pkg("S15"), "strategy", "Sales Overview")["filters"][0]["values"] == ["2024"]


def test_lineage_and_expert_sql():
    t = trace_lineage_impl(pkg("S01"), "strategy", "Order Date")
    assert set(t["equivalent_columns"]) == {"retail.fact_orders.order_date", "retail.dim_date.date_key"}
    assert trace_lineage_impl(pkg("S03"), "strategy", "Revenue")["physical_columns"] == ["retail.fact_orders.order_amount"]
    out = analyze_sql_impl("strategy", "SELECT o.a AS x FROM retail.t o WHERE o.b = 1")
    assert out["outputs"] == {"x": "retail.t.a"} and out["where"] == ["retail.t.b = 1"]


def test_compare_fields():
    assert compare_fields_impl(pkg("S01"), "Discount Rate", "Discount Rate")["identical"] is True
    d = compare_fields_impl(pkg("S11"), "Discount Rate", "Discount Rate")["difference"]
    assert (d["check"], d["status"]) == ("FX-03", "FAIL")
    d = compare_fields_impl(pkg("S14"), "Net Revenue", "Net Revenue")["difference"]
    assert (d["check"], d["status"]) == ("FX-01", "UNVERIFIED")
    assert compare_fields_impl(pkg("S15"), "Order Week", "Order Week")["difference"]["check"] == "LN-01"


def test_matching():
    m = match_fields_impl(pkg("S02"))
    assert {"thoughtspot": "Units", "strategy": "Units Sold", "basis": "mapped", "score": 1.0} in m["pairs"]
    assert match_fields_impl(pkg("S17"))["only_in_thoughtspot"] == ["Shipping"]
    assert find_counterparts_impl(pkg("S02"), "Line Revenue")["candidates"][0]["strategy"] == "Gross Sales"


def test_joins():
    issues = [c["issues"] for c in compare_joins_impl(pkg("S05"))["comparisons"] if c["issues"] != "none"]
    assert issues and issues[0][0]["check"] == "JN-01"
    assert all(c["issues"] == "none" for c in compare_joins_impl(pkg("S01"))["comparisons"])
    assert {t["verdict"] for t in analyze_row_multiplication_impl(pkg("S13"))["tables"]} == {"chasm"}
    assert analyze_row_multiplication_impl(pkg("S04"))["tables"][0]["verdict"] == "unknown"
    g = get_join_graph_impl(pkg("S01"), "strategy")
    assert all(j["origin"] == "derived" and j["type"] == "LEFT" for j in g["joins"])


def test_filters_sweep_probe():
    assert compare_filters_impl(pkg("S03"))["matched"] and not compare_filters_impl(pkg("S10"))["matched"]
    assert compare_filters_impl(pkg("S01"))["matched"]  # the filtered fact table's WHERE
    sweep = run_rule_sweep_impl(pkg("S07"))
    assert [l["check"] for l in sweep["open_leads"]] == ["AG-01"] and sweep["proven_by_rules"]
    assert "error" in request_data_probe_impl(pkg("S04"), "key_profile")  # needs source and keys


def test_tools_are_strands_tools():
    from inspector_agent.inspector_agent import TOOLS

    names = [t.tool_name for t in TOOLS]
    assert len(names) == len(set(names)) == 18
    assert all(t.tool_spec["description"] and t.tool_spec["inputSchema"] for t in TOOLS)


@needs_warehouse
def test_request_data_probe_runs_lead_and_kpi_probes():
    from inspector_agent.tools.rule_sweep.tool import sweep

    lead = next(f for f in sweep(pkg("S04")) if f.check == "JN-04")
    r = request_data_probe_impl(pkg("S04"), "lead", lead_id=lead.id)
    assert r["match"] is False and "not unique" in r["summary"]
    r = request_data_probe_impl(pkg("S14"), "kpi_equivalence", thoughtspot_field="Net Revenue",
                                strategy_field="Net Revenue")
    assert r["match"] is True and r["subject"]["thoughtspot"] == "Net Revenue"
    r = request_data_probe_impl(pkg("S18"), "kpi_equivalence", thoughtspot_field="Net Revenue",
                                strategy_field="Net Revenue")
    assert r["match"] is None  # no definition in the Ossie export: data cannot decide
