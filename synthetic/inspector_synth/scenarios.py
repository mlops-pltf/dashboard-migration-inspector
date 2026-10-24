"""Scenarios S01-S18: a ThoughtSpot model + content, the Strategy version a developer built, and ground truth.

Ground truth per scenario:
  * expected findings (check id, severity, status, objects, root cause and symptoms, acceptable alternatives)
  * ``must_pass``: fields that must come out PASS (precision: flagging them is a false positive)
  * witnesses: query pairs whose results must differ (defect) or match (benign) on the synthetic warehouse
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import domain as d
from .domain import Filter, Liveboard, SemanticModel, StDef
from .sqlgen import Semantics, freeform_sql, from_ffsql, from_model
from .strategy import StrategySpec


@dataclass(frozen=True)
class Expected:
    id: str
    check: str
    severity: str  # BLOCKER | WARNING | LOW | INFO
    status: str  # FAIL | PASS | UNVERIFIED
    thoughtspot: str | None
    strategy: str | None
    note: str
    root_cause: bool = False
    symptoms: tuple[str, ...] = ()
    also_acceptable: tuple[str, ...] = ()
    latent: bool = False
    status_after_data: str | None = None
    decided_by: str | None = None  # rule | llm+data | data


@dataclass(frozen=True)
class Query:
    dims: tuple[str, ...]
    measures: tuple[str, ...]
    filters: tuple[Filter, ...] = ()
    sort: tuple[str, str] | None = None
    top_n: int | None = None


@dataclass(frozen=True)
class Witness:
    id: str
    description: str
    expect: str  # differ | equal
    thoughtspot: Query
    strategy: Query | None = None  # defaults to the ThoughtSpot query


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    ts_model: SemanticModel
    ts_content: tuple[Liveboard, ...]
    strategy: StrategySpec
    strategy_format: str  # yaml_export | rest_json | ossie_yaml
    expected_verdict: str
    expected: tuple[Expected, ...] = ()
    must_pass: tuple[str, ...] = ()
    witnesses: tuple[Witness, ...] = ()
    expected_verdict_after_data: str | None = None
    mapping: dict | None = None

    def ts_semantics(self) -> Semantics:
        return from_model(self.ts_model)

    def strategy_semantics(self) -> Semantics:
        s = self.strategy
        if s.style == "ffsql":
            return from_ffsql(s.model, s.ffsql.sql, s.ffsql.colmap)
        return from_model(s.model)  # lookup style preserves outer joins, i.e. the model's join types


def _lookup(model: SemanticModel, **kw) -> StrategySpec:
    kw.setdefault("content", d.CONTENT[model.name])
    return StrategySpec(name=kw.pop("name", model.name), model=model, style="lookup", **kw)


def _ffsql(model: SemanticModel, ff_kwargs: dict | None = None, **kw) -> StrategySpec:
    kw.setdefault("content", d.CONTENT[model.name])
    return StrategySpec(name=kw.pop("name", model.name), model=model, style="ffsql",
                        ffsql=freeform_sql(model, **(ff_kwargs or {})), **kw)


def _q(dims, measures, **kw) -> Query:
    return Query(tuple(dims), tuple(measures), **kw)


ORDER_MEASURES = ("Revenue", "Discount", "Shipping", "Items Sold", "Order Count", "Customer Count",
                  "Avg Order Value", "Discount Rate", "Net Revenue")
S = d.SALES
REGION_REVENUE_Q = _q(("Store Region", "Store Type"), ("Revenue", "Region Revenue"))


def _sales_must_pass(*excluding: str) -> tuple[str, ...]:
    return tuple(f"Sales Orders::{m}" for m in (*ORDER_MEASURES, "Region Revenue") if m not in excluding)


def build() -> list[Scenario]:
    out: list[Scenario] = []

    # S01 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S01", "Clean lift-and-shift of a Worksheet",
        "Sales Orders rebuilt as a lookup-table Mosaic model with identical joins, aggregations and filters.",
        S, d.CONTENT["Sales Orders"], _lookup(S), "yaml_export", "READY",
        must_pass=_sales_must_pass(),
        witnesses=(
            Witness("W1", "All order measures by Customer Region (includes orphan customers)", "equal",
                    _q(("Customer Region",), ORDER_MEASURES)),
            Witness("W2", "Region Revenue at Store Region x Store Type", "equal", REGION_REVENUE_Q),
        ),
    ))

    # S02 ------------------------------------------------------------------------------------
    renames = {"Units": "Units Sold", "Line Revenue": "Gross Sales", "Gross Margin": "Margin Amount",
               "Category": "Product Category"}
    out.append(Scenario(
        "S02", "Clean lift-and-shift of a Model with deliberate renames",
        "Order Lines rebuilt faithfully; four objects renamed and declared in mapping.yaml. Delivered as REST JSON.",
        d.LINES, d.CONTENT["Order Lines"],
        _lookup(d.LINES, name="Product Lines", renames=renames), "rest_json", "READY",
        must_pass=tuple(f"Order Lines::{m}" for m in
                        ("Units", "Line Revenue", "Line Cost", "Gross Margin", "Margin %", "Avg Selling Price")),
        witnesses=(Witness("W1", "Margin by Category", "equal",
                           _q(("Category",), ("Line Revenue", "Gross Margin", "Margin %", "Units"))),),
        mapping={
            "models": [{"thoughtspot": "Order Lines", "strategy": "Product Lines"}],
            "fields": [{"thoughtspot": f"Order Lines::{k}", "strategy": f"Product Lines::{v}"}
                       for k, v in renames.items()],
        },
    ))

    # S03 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S03", "Benign restructure: Worksheet -> one equivalent Freeform SQL",
        "All joins kept LEFT, filter moved into the WHERE clause. Structurally different, semantically identical.",
        S, d.CONTENT["Sales Orders"], _ffsql(S), "yaml_export", "READY",
        must_pass=_sales_must_pass(),
        witnesses=(
            Witness("W1", "All order measures by Customer Region", "equal", _q(("Customer Region",), ORDER_MEASURES)),
            Witness("W2", "All order measures, grand total", "equal", _q((), ORDER_MEASURES)),
            Witness("W3", "Region Revenue at Store Region x Store Type", "equal", REGION_REVENUE_Q),
        ),
    ))

    # S04 ------------------------------------------------------------------------------------
    fanout = _ffsql(S, {
        "extra_joins": ("JOIN analytics.retail.fact_order_lines l ON l.order_id = o.order_id",
                        "LEFT JOIN analytics.retail.dim_product p ON p.product_id = l.product_id"),
        "extra_columns": (("p.category", "product_category"),),
    }, extra_attributes=(("Product Category", "product_category"),))
    out.append(Scenario(
        "S04", "Fan-out: Freeform SQL joins orders to order lines",
        "The developer added Product Category by joining order lines into the order-grain SQL. "
        "Every additive order measure is now repeated once per line.",
        S, d.CONTENT["Sales Orders"], fanout, "yaml_export", "NEEDS_REVIEW",
        expected=(
            Expected("E1", "JN-04", "BLOCKER", "UNVERIFIED", "Sales Orders::Revenue", "Sales Orders::Revenue",
                     "join to fact_order_lines on order_id; nothing in the package says order_id is unique there, "
                     "so the fan-out is confirmed by a key profile (data)", root_cause=True,
                     symptoms=("Discount", "Shipping", "Items Sold", "Avg Order Value", "Discount Rate",
                               "Net Revenue", "Region Revenue"), status_after_data="FAIL", decided_by="rule+data"),
            Expected("E2", "CV-02", "INFO", "FAIL", None, "Sales Orders::Product Category",
                     "Extra attribute with no ThoughtSpot counterpart"),
        ),
        must_pass=("Sales Orders::Order Count", "Sales Orders::Customer Count"),
        witnesses=(
            Witness("W1", "Revenue by Store Region (inflated)", "differ", _q(("Store Region",), ("Revenue",))),
            Witness("W2", "Order Count by Store Region (COUNT DISTINCT is fan-out safe)", "equal",
                    _q(("Store Region",), ("Order Count", "Customer Count"))),
        ),
        expected_verdict_after_data="NOT_READY",
    ))

    # S05 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S05", "LEFT -> INNER join to customers, with orphan customers",
        "The Freeform SQL inner-joins dim_customer; ~3% of orders reference customers missing from the dimension.",
        S, d.CONTENT["Sales Orders"], _ffsql(S, {"join_types": {"DIM_CUSTOMER": "INNER"}}), "yaml_export",
        "NOT_READY",
        expected=(Expected("E1", "JN-01", "BLOCKER", "FAIL", "Sales Orders::FACT_ORDERS_to_DIM_CUSTOMER",
                           "Sales Orders SQL", "INNER JOIN dim_customer drops orders with unknown customers",
                           root_cause=True, symptoms=ORDER_MEASURES, also_acceptable=("JN-06", "FL-03"),
                           decided_by="rule"),),
        witnesses=(
            Witness("W1", "Revenue grand total", "differ", _q((), ("Revenue", "Order Count"))),
            Witness("W2", "Revenue by Store Region", "differ", _q(("Store Region",), ("Revenue",))),
        ),
    ))

    # S06 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S06", "LEFT -> INNER join to dates, no orphan dates (latent)",
        "Same defect class as S05, but dim_date is complete: today's numbers match, tomorrow's may not.",
        S, d.CONTENT["Sales Orders"], _ffsql(S, {"join_types": {"DIM_DATE": "INNER"}}), "yaml_export",
        "NOT_READY",
        expected=(Expected("E1", "JN-01", "BLOCKER", "FAIL", "Sales Orders::FACT_ORDERS_to_DIM_DATE",
                           "Sales Orders SQL", "INNER JOIN dim_date; no current impact because dim_date is complete",
                           root_cause=True, latent=True, also_acceptable=("JN-06", "FL-03"), decided_by="rule+data"),),
        witnesses=(Witness("W1", "Revenue by Store Region (no impact today)", "equal",
                           _q(("Store Region",), ("Revenue", "Order Count"))),),
    ))

    # S07 ------------------------------------------------------------------------------------
    s07 = S.with_field("Customer Count", st=StDef("fact_metric", "customer_id", "count", columns=("customer_id",)),
                       sql="COUNT({o.customer_id})")
    out.append(Scenario(
        "S07", "COUNT instead of COUNT DISTINCT",
        "Customer Count is built as a plain count of customer_id.",
        S, d.CONTENT["Sales Orders"], _lookup(s07), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "AG-01", "BLOCKER", "FAIL", "Sales Orders::Customer Count",
                           "Sales Orders::Customer Count", "count vs unique count", root_cause=True,
                           decided_by="rule"),),
        must_pass=_sales_must_pass("Customer Count"),
        witnesses=(Witness("W1", "Customer Count by Store Region", "differ",
                           _q(("Store Region",), ("Customer Count",))),),
    ))

    # S08 ------------------------------------------------------------------------------------
    s08 = S.with_field("Region Revenue", st=StDef("fact_metric", "order_amount", "sum", columns=("order_amount",)),
                       level=())
    out.append(Scenario(
        "S08", "group_aggregate level lost",
        "Region Revenue is built as an ordinary report-level sum instead of a Store Region level metric.",
        S, d.CONTENT["Sales Orders"], _lookup(s08), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "AG-02", "BLOCKER", "FAIL", "Sales Orders::Region Revenue",
                           "Sales Orders::Region Revenue", "fixed level {Store Region} vs report level",
                           root_cause=True, decided_by="rule"),),
        must_pass=_sales_must_pass("Region Revenue"),
        witnesses=(Witness("W1", "Region Revenue at Store Region x Store Type", "differ", REGION_REVENUE_Q),),
    ))

    # S09 ------------------------------------------------------------------------------------
    s09 = d.LINES.with_field(
        "Margin %",
        st=StDef("fact_metric", 'ApplySimple("(#0 - #1) / NULLIF(#0, 0)", line_amount, line_cost)', "avg",
                 columns=("line_amount", "line_cost")),
        sql="AVG(({l.line_amount} - {l.line_cost}) / NULLIF({l.line_amount}, 0))")
    out.append(Scenario(
        "S09", "Ratio of sums vs average of row ratios",
        "Margin % is built as the average of per-line margins instead of total margin / total revenue.",
        d.LINES, d.CONTENT["Order Lines"], _lookup(s09), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "AG-03", "BLOCKER", "FAIL", "Order Lines::Margin %", "Order Lines::Margin %",
                           "avg(row ratio) vs sum/sum", root_cause=True, decided_by="rule"),),
        must_pass=tuple(f"Order Lines::{m}" for m in ("Units", "Line Revenue", "Line Cost", "Gross Margin",
                                                       "Avg Selling Price")),
        witnesses=(Witness("W1", "Margin % by Category", "differ", _q(("Category",), ("Margin %",))),),
    ))

    # S10 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S10", "Worksheet filter dropped",
        "The Freeform SQL has no WHERE clause, so cancelled orders are counted.",
        S, d.CONTENT["Sales Orders"], _ffsql(S, {"where": False}), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "FL-01", "BLOCKER", "FAIL", "Sales Orders::filter Order Status not in (Cancelled)",
                           "Sales Orders SQL", "worksheet filter has no equivalent", root_cause=True,
                           symptoms=ORDER_MEASURES, decided_by="rule"),),
        witnesses=(Witness("W1", "Revenue by Store Region", "differ", _q(("Store Region",), ("Revenue", "Order Count"))),),
    ))

    # S11 ------------------------------------------------------------------------------------
    s11 = S.with_field("Discount Rate", st=StDef("metric", "[Discount] / [Revenue]"),
                       sql="SUM({o.discount_amount}) / NULLIF(SUM({o.order_amount}), 0)")
    out.append(Scenario(
        "S11", "safe_divide vs plain division",
        "Discount Rate uses plain division: 'Replacement' orders (revenue 0) give NULL instead of 0.",
        S, d.CONTENT["Sales Orders"], _lookup(s11), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "FX-03", "BLOCKER", "FAIL", "Sales Orders::Discount Rate",
                           "Sales Orders::Discount Rate", "safe_divide returns 0 when revenue is 0; Strategy returns NULL",
                           root_cause=True, also_acceptable=("FX-01",), decided_by="rule"),),
        must_pass=_sales_must_pass("Discount Rate"),
        witnesses=(Witness("W1", "Discount Rate by Order Status", "differ", _q(("Order Status",), ("Discount Rate",))),),
    ))

    # S12 ------------------------------------------------------------------------------------
    s12_ts = S.with_join_type("DIM_CUSTOMER", "INNER")
    out.append(Scenario(
        "S12", "Progressive INNER join vs always-applied INNER join",
        "ThoughtSpot inner-joins customers only when a customer column is used; the Freeform SQL always does.",
        s12_ts, d.CONTENT["Sales Orders"], _ffsql(s12_ts), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "JN-06", "BLOCKER", "FAIL", "Sales Orders::FACT_ORDERS_to_DIM_CUSTOMER",
                           "Sales Orders SQL", "orphan-customer orders vanish even without customer columns",
                           root_cause=True, symptoms=ORDER_MEASURES, also_acceptable=("FL-03",),
                           decided_by="rule"),),
        witnesses=(
            Witness("W1", "Revenue by Store Region (no customer column)", "differ",
                    _q(("Store Region",), ("Revenue",))),
            Witness("W2", "Revenue by Customer Region (both sides inner-join)", "equal",
                    _q(("Customer Region",), ("Revenue",))),
        ),
    ))

    # S13 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S13", "Chasm trap: two facts flattened through a shared dimension",
        "Lines and returns are joined through dim_product in one Freeform SQL.",
        d.RETURNS, d.CONTENT["Returns Analysis"], _ffsql(d.RETURNS), "yaml_export", "NOT_READY",
        expected=(Expected("E1", "JN-05", "BLOCKER", "FAIL", "Returns Analysis", "Returns Analysis SQL",
                           "fact_order_lines x fact_returns via product_id", root_cause=True,
                           symptoms=("Line Revenue", "Units Sold", "Refund Amount", "Units Returned"),
                           also_acceptable=("JN-04",), decided_by="rule"),
                  Expected("E2", "JN-01", "BLOCKER", "FAIL", "Returns Analysis::FACT_RETURNS_to_DIM_PRODUCT",
                           "Returns Analysis SQL", "dim_product LEFT JOIN fact_returns: seen from the returns this is a "
                           "RIGHT join, so refunds for products missing from dim_product are dropped",
                           symptoms=("Refund Amount", "Units Returned"), decided_by="rule"),),
        witnesses=(Witness("W1", "Revenue vs Refunds by Category", "differ",
                           _q(("Category",), ("Line Revenue", "Refund Amount"))),),
    ))

    # S14 ------------------------------------------------------------------------------------
    s14 = S.with_field("Net Revenue", st=StDef("metric", "[Revenue] - NullToZero([Discount])"),
                       sql="SUM({o.order_amount}) - COALESCE(SUM({o.discount_amount}), 0)")
    out.append(Scenario(
        "S14", "Different formula, same meaning",
        "Net Revenue = Revenue - NullToZero(Discount) instead of sum(amount - ifnull(discount, 0)).",
        S, d.CONTENT["Sales Orders"], _lookup(s14), "yaml_export", "NEEDS_REVIEW",
        expected=(Expected("E1", "FX-01", "BLOCKER", "UNVERIFIED", "Sales Orders::Net Revenue",
                           "Sales Orders::Net Revenue", "algebraically equal (SUM is linear, NULLs handled)",
                           status_after_data="PASS", decided_by="llm+data"),),
        must_pass=_sales_must_pass("Net Revenue"),
        witnesses=(Witness("W1", "Net Revenue by Order Week and Customer Segment", "equal",
                           _q(("Order Week", "Customer Segment"), ("Net Revenue",))),),
        expected_verdict_after_data="READY",
    ))

    # S15 ------------------------------------------------------------------------------------
    s15 = S.with_field("Order Week", column="week_start_sunday", st=StDef("attribute", "week_start_sunday"),
                       sql="{d.week_start_sunday}")
    lb = d.SALES_OVERVIEW
    s15_lb = replace(lb, filters=(Filter("Order Year", "in", ("2024",)),),
                     visualizations=tuple(replace(v, top_n=5) if v.id == "V3" else v for v in lb.visualizations))
    out.append(Scenario(
        "S15", "Dashboard drift: default filter, top-N and week start",
        "Default year filter 2025 -> 2024, Top Customers 10 -> 5, Order Week starts Sunday instead of Monday.",
        S, d.CONTENT["Sales Orders"], _lookup(s15, content=(s15_lb, d.SEGMENT_ANSWER)), "yaml_export", "NOT_READY",
        expected=(
            Expected("E1", "LN-01", "BLOCKER", "FAIL", "Sales Orders::Order Week", "Sales Orders::Order Week",
                     "week_start_monday vs week_start_sunday", root_cause=True,
                     symptoms=("Sales Overview::Weekly Net Revenue",), also_acceptable=("FX-04",), decided_by="rule"),
            Expected("E2", "DB-02", "BLOCKER", "FAIL", "Sales Overview::filter Order Year",
                     "Sales Overview::filter Order Year", "2025 vs 2024", decided_by="rule"),
            Expected("E3", "DB-05", "BLOCKER", "FAIL", "Sales Overview::Top Customers",
                     "Sales Overview::Top Customers", "top 10 vs top 5", decided_by="rule"),
        ),
        must_pass=_sales_must_pass(),
        witnesses=(
            Witness("W1", "Net Revenue by Order Week", "differ", _q(("Order Week",), ("Net Revenue",))),
            Witness("W2", "Top Customers as each dashboard shows it", "differ",
                    _q(("Customer Name",), ("Revenue",), filters=lb.filters, sort=("Revenue", "DESC"), top_n=10),
                    _q(("Customer Name",), ("Revenue",), filters=s15_lb.filters, sort=("Revenue", "DESC"), top_n=5)),
        ),
    ))

    # S16 ------------------------------------------------------------------------------------
    s16_lb = replace(lb, visualizations=tuple(
        replace(v, chart="BAR") if v.id == "V1" else
        replace(v, title="Revenue Trend by Month") if v.id == "V2" else
        replace(v, chart="COLUMN") if v.id == "V5" else v for v in lb.visualizations))
    out.append(Scenario(
        "S16", "Cosmetic differences only",
        "Chart types and a title changed; every number is identical.",
        S, d.CONTENT["Sales Orders"], _lookup(S, content=(s16_lb, d.SEGMENT_ANSWER)), "yaml_export", "READY",
        expected=(
            Expected("E1", "LF-01", "LOW", "FAIL", "Sales Overview::Revenue by Store Region",
                     "Sales Overview::Revenue by Store Region", "COLUMN vs horizontal bar"),
            Expected("E2", "LF-02", "LOW", "FAIL", "Sales Overview::Monthly Revenue Trend",
                     "Sales Overview::Revenue Trend by Month", "title changed"),
            Expected("E3", "LF-01", "LOW", "FAIL", "Sales Overview::Discount Rate by Status",
                     "Sales Overview::Discount Rate by Status", "table vs column chart"),
        ),
        must_pass=_sales_must_pass(),
        witnesses=(Witness("W1", "Revenue by Store Region", "equal", _q(("Store Region",), ("Revenue",))),),
    ))

    # S17 ------------------------------------------------------------------------------------
    s17 = S.without_field("Shipping")
    s17_lb = replace(lb, visualizations=tuple(
        replace(v, measures=("Customer Count",)) if v.id == "V8" else v for v in lb.visualizations))
    out.append(Scenario(
        "S17", "Missing KPI used by content, plus an extra KPI",
        "Shipping was not migrated (the dashboard silently dropped it); 'Revenue per Item' was added.",
        S, d.CONTENT["Sales Orders"],
        _lookup(s17, content=(s17_lb, d.SEGMENT_ANSWER), extra_metrics=(("Revenue per Item", "[Revenue] / [Items Sold]"),)),
        "yaml_export", "NOT_READY",
        expected=(
            Expected("E1", "CV-01", "BLOCKER", "FAIL", "Sales Orders::Shipping", None,
                     "no counterpart; used by Sales Overview::Shipping by Customer Region", root_cause=True,
                     symptoms=("Sales Overview::Shipping by Customer Region",), decided_by="rule"),
            Expected("E2", "DB-01", "BLOCKER", "FAIL", "Sales Overview::Shipping by Customer Region",
                     "Sales Overview::Shipping by Customer Region", "measure Shipping missing from the visualization",
                     decided_by="rule"),
            Expected("E3", "CV-02", "INFO", "FAIL", None, "Sales Orders::Revenue per Item", "extra metric"),
        ),
        must_pass=_sales_must_pass("Shipping"),
    ))

    # S18 ------------------------------------------------------------------------------------
    out.append(Scenario(
        "S18", "Ossie YAML input for the S08 model",
        "Same defect as S08, delivered as Apache Ossie YAML, which drops metric levels and Apply* expressions.",
        S, d.CONTENT["Sales Orders"], _lookup(s08), "ossie_yaml", "NEEDS_REVIEW",
        expected=(
            Expected("E1", "AG-02", "BLOCKER", "UNVERIFIED", "Sales Orders::Region Revenue",
                     "Sales Orders::Region Revenue", "level information not present in Ossie YAML",
                     status_after_data="FAIL", decided_by="data"),
            Expected("E2", "FX-01", "BLOCKER", "UNVERIFIED", "Sales Orders::Net Revenue",
                     "Sales Orders::Net Revenue", "ApplySimple metric exported without an expression; with no "
                     "definition there is nothing to run, so data cannot settle it (needs Strategy's report SQL)",
                     status_after_data="UNVERIFIED", decided_by="data"),
            Expected("E3", "JN-01", "BLOCKER", "UNVERIFIED", "Sales Orders::FACT_ORDERS_to_DIM_CUSTOMER",
                     "Sales Orders::fact_orders_to_dim_customer", "Ossie relationships carry no join type; 749 "
                     "orders have no customer, so the result depends on the type and data cannot settle it",
                     status_after_data="UNVERIFIED", decided_by="data"),
        ),
        must_pass=_sales_must_pass("Region Revenue", "Net Revenue"),
        witnesses=(
            Witness("W1", "Region Revenue at Store Region x Store Type", "differ", REGION_REVENUE_Q),
            Witness("W2", "Net Revenue by Customer Segment", "equal", _q(("Customer Segment",), ("Net Revenue",))),
        ),
        expected_verdict_after_data="NOT_READY",
    ))
    return out


SCENARIOS = {s.id: s for s in build()}
