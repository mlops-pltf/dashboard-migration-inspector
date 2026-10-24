import httpx
import pytest

from data_agent.data_agent import SKILLS, app
from data_agent.tools.profiler import profile
from data_agent.tools.reconciler.reconcile import compare_queries
from data_agent.tools.sql_guard.guard import guard
from data_agent.tools.warehouse.connection import connect
from data_agent.tools.warehouse.tool import describe_table_impl, run_query_impl
from inspector_contract.a2a import A2AClient


def _warehouse_up() -> bool:
    try:
        connect().close()
        return True
    except Exception:
        return False


needs_warehouse = pytest.mark.skipif(not _warehouse_up(), reason="warehouse not reachable (docker compose up warehouse)")

REV_BY_REGION = ("SELECT s.region, SUM(o.order_amount) AS rev FROM retail.fact_orders o "
                 "JOIN retail.dim_store s ON o.store_id = s.store_id {where} GROUP BY 1")


# --- guard (no warehouse needed) ------------------------------------------------------------------
@pytest.mark.parametrize("sql, reason", [
    ("DELETE FROM retail.fact_orders", "only SELECT"),
    ("SELECT 1; SELECT 2", "exactly one statement"),
    ("SELECT * FROM pg_catalog.pg_user", "schema pg_catalog"),
    ("SELECT * FROM fact_orders", "schema-qualified"),
    ("SELECT pg_sleep(5)", "pg_sleep"),
    ("SELECT * INTO retail.copy FROM retail.fact_orders", "not allowed"),
    ("UNLOAD ('select 1') TO 's3://x'", ""),
])
def test_guard_rejects(sql, reason):
    g = guard(sql)
    assert not g.ok
    assert reason in " ".join(g.reasons)


def test_guard_transpiles_redshift_and_allows_ctes():
    g = guard("WITH x AS (SELECT NVL(order_amount, 0) a FROM retail.fact_orders) SELECT GETDATE(), a FROM x")
    assert g.ok and g.tables == ["retail.fact_orders"]
    assert "COALESCE" in g.sql and "CURRENT_TIMESTAMP" in g.sql


# --- warehouse ------------------------------------------------------------------------------------
@needs_warehouse
def test_run_query_caps_rows():
    r = run_query_impl("SELECT order_status, COUNT(*) n FROM retail.fact_orders GROUP BY 1", max_rows=2)
    assert r["row_count"] == 2 and r["truncated"] and r["columns"] == ["order_status", "n"]
    assert run_query_impl("DROP TABLE retail.fact_orders")["error"] == "query rejected"


@needs_warehouse
def test_describe_table():
    d = describe_table_impl("retail.dim_store")
    assert d["row_count"] > 0 and d["columns"][0]["name"] == "store_id"
    assert "error" in describe_table_impl("pg_catalog.pg_user")


@needs_warehouse
def test_key_profile_detects_duplicates():
    assert profile.key_profile("retail.dim_customer", ["customer_id"])["unique"]
    p = profile.key_profile("retail.fact_order_lines", ["order_id"])
    assert not p["unique"] and p["max_rows_per_key"] > 1 and p["duplicate_samples"]


@needs_warehouse
def test_orphan_profile_counts_unmatched_rows():
    p = profile.orphan_profile("retail.fact_orders", "retail.dim_customer", ["customer_id"])
    assert p["left_side"]["orphan_rows"] > 0  # planted: orders of unknown customers
    assert p["right_side"]["rows"] > 0


@needs_warehouse
def test_orphan_profile_accepts_sql_sources():
    p = profile.orphan_profile("SELECT order_id FROM retail.fact_orders WHERE order_status = 'Completed'",
                               "retail.fact_orders", ["order_id"])
    assert p["left_side"]["orphan_rows"] == 0


@needs_warehouse
def test_compare_queries_match_and_mismatch():
    same = compare_queries(REV_BY_REGION.format(where=""), REV_BY_REGION.format(where=""), ["region"], ["rev"])
    assert same["status"] == "MATCH" and same["matching_rows"] == same["rows"] > 0
    diff = compare_queries(REV_BY_REGION.format(where=""),
                           REV_BY_REGION.format(where="WHERE o.order_status = 'Completed'"), ["region"], ["rev"])
    assert diff["status"] == "MISMATCH" and diff["measures"][0]["mismatched_rows"] == diff["rows"]
    assert diff["measures"][0]["total_difference"] < 0 and diff["mismatch_samples"]


@needs_warehouse
def test_compare_queries_one_sided_rows_and_grand_total():
    r = compare_queries("SELECT order_status, COUNT(*) n FROM retail.fact_orders GROUP BY 1",
                        "SELECT order_status, COUNT(*) n FROM retail.fact_orders WHERE order_status <> 'Pending' "
                        "GROUP BY 1", ["order_status"], ["n"])
    assert r["left_only_rows"] == 1 and r["status"] == "MISMATCH"
    g = compare_queries("SELECT SUM(order_amount) x FROM retail.fact_orders",
                        "SELECT SUM(order_amount) + 0.001 y FROM retail.fact_orders", [], [["x", "y"]])
    assert g["status"] == "MATCH" and g["keys"] == []


# --- A2A ------------------------------------------------------------------------------------------
@needs_warehouse
def test_a2a_structured_skills():
    client = A2AClient("http://data/", transport=httpx.ASGITransport(app=app(conversation=False)))
    assert {s.id for s in client.card().skills} == set(SKILLS) | {"ask"}
    r = client.call("key_profile", {"source": "retail.dim_customer", "keys": ["customer_id"]})
    assert r["unique"] is True
    assert "error" in client.call("run_query", {"sql": "DELETE FROM retail.fact_orders"})
