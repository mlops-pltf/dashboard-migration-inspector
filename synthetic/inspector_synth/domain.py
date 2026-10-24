"""The ThoughtSpot-side semantic spec: tables, joins, worksheets/models, liveboards.

Every field carries three views of the same meaning:
  * ``ts``  - how ThoughtSpot defines it (column or formula text with ``{TABLE}`` placeholders)
  * ``st``  - how a *correct* Strategy developer builds it (attribute / fact metric / metric)
  * ``sql`` - its true semantics as SQL over ``{alias.column}`` placeholders, used for witness queries

Scenarios copy these specs and corrupt the Strategy side (``st`` + ``sql`` together).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Table name (ThoughtSpot object) -> (physical table, SQL alias)
TABLE_INFO: dict[str, tuple[str, str]] = {
    "FACT_ORDERS": ("fact_orders", "o"),
    "FACT_ORDER_LINES": ("fact_order_lines", "l"),
    "FACT_RETURNS": ("fact_returns", "r"),
    "DIM_CUSTOMER": ("dim_customer", "c"),
    "DIM_STORE": ("dim_store", "s"),
    "DIM_PRODUCT": ("dim_product", "p"),
    "DIM_DATE": ("dim_date", "d"),
}
ALIAS_TO_TABLE = {alias: name for name, (_, alias) in TABLE_INFO.items()}

# ThoughtSpot Table TML columns: TS table -> [(display name, physical column, MEASURE agg or None)]
TABLE_COLUMNS: dict[str, list[tuple[str, str, str | None]]] = {
    "FACT_ORDERS": [
        ("Order ID", "order_id", None), ("Order Timestamp", "order_ts", None),
        ("Order Date", "order_date", None), ("Customer ID", "customer_id", None),
        ("Store ID", "store_id", None), ("Order Status", "order_status", None),
        ("Order Amount", "order_amount", "SUM"), ("Discount Amount", "discount_amount", "SUM"),
        ("Shipping Amount", "shipping_amount", "SUM"), ("Item Count", "item_count", "SUM"),
    ],
    "FACT_ORDER_LINES": [
        ("Line ID", "line_id", None), ("Order ID", "order_id", None), ("Line Number", "line_number", None),
        ("Product ID", "product_id", None), ("Quantity", "quantity", "SUM"),
        ("Unit Price", "unit_price", "AVERAGE"), ("Line Amount", "line_amount", "SUM"),
        ("Line Cost", "line_cost", "SUM"),
    ],
    "FACT_RETURNS": [
        ("Return ID", "return_id", None), ("Order ID", "order_id", None), ("Product ID", "product_id", None),
        ("Return Date", "return_date", None), ("Return Quantity", "return_qty", "SUM"),
        ("Refund Amount", "refund_amount", "SUM"),
    ],
    "DIM_CUSTOMER": [
        ("Customer ID", "customer_id", None), ("Customer Name", "customer_name", None),
        ("Segment", "segment", None), ("Region", "region", None), ("Signup Date", "signup_date", None),
    ],
    "DIM_STORE": [
        ("Store ID", "store_id", None), ("Store Name", "store_name", None), ("City", "city", None),
        ("State", "state", None), ("Region", "region", None), ("Store Type", "store_type", None),
    ],
    "DIM_PRODUCT": [
        ("Product ID", "product_id", None), ("Product Name", "product_name", None),
        ("Category", "category", None), ("Subcategory", "subcategory", None), ("Brand", "brand", None),
        ("Unit Cost", "unit_cost", "SUM"),
    ],
    "DIM_DATE": [
        ("Date", "date_key", None), ("Day of Week", "day_of_week", None),
        ("Week Start Monday", "week_start_monday", None), ("Week Start Sunday", "week_start_sunday", None),
        ("Month Start", "month_start", None), ("Month Name", "month_name", None), ("Quarter", "quarter", None),
        ("Year", "year", None), ("Fiscal Year", "fiscal_year", None), ("Fiscal Quarter", "fiscal_quarter", None),
    ],
}


def table_column(table: str, physical: str) -> str:
    """ThoughtSpot display name of a physical column."""
    return next(name for name, col, _ in TABLE_COLUMNS[table] if col == physical)


@dataclass(frozen=True)
class Join:
    source: str
    destination: str
    keys: tuple[tuple[str, str], ...]  # (source physical col, destination physical col)
    type: str = "LEFT_OUTER"  # INNER | LEFT_OUTER
    cardinality: str = "MANY_TO_ONE"

    @property
    def name(self) -> str:
        return f"{self.source}_to_{self.destination}"


TABLE_JOINS: tuple[Join, ...] = (
    Join("FACT_ORDERS", "DIM_CUSTOMER", (("customer_id", "customer_id"),)),
    Join("FACT_ORDERS", "DIM_STORE", (("store_id", "store_id"),)),
    Join("FACT_ORDERS", "DIM_DATE", (("order_date", "date_key"),)),
    Join("FACT_ORDER_LINES", "FACT_ORDERS", (("order_id", "order_id"),)),
    Join("FACT_ORDER_LINES", "DIM_PRODUCT", (("product_id", "product_id"),)),
    Join("FACT_RETURNS", "DIM_PRODUCT", (("product_id", "product_id"),)),
)


@dataclass(frozen=True)
class StDef:
    """How a Strategy developer builds the field.

    attribute:   expr = physical column on ``table``
    fact_metric: expr = fact expression text over physical columns of the home table;
                 function = sum | count | avg | min | max; level = attribute names (dimensionality)
    metric:      expr = metric expression text referencing other objects as [Name]
    """

    kind: str
    expr: str
    function: str | None = None
    distinct: bool = False
    level: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()  # physical columns a fact expression reads


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # ATTRIBUTE | MEASURE
    st: StDef
    sql: str  # attribute: "{alias.col}"; measure: aggregate SQL over placeholders
    table: str | None = None  # column-backed fields: TS table
    column: str | None = None  # column-backed fields: physical column
    formula: str | None = None  # formula-backed fields: TS formula with {TABLE} placeholders
    aggregation: str | None = None  # column-backed measures
    level: tuple[str, ...] = ()  # fixed-level dimensions (group_aggregate)
    description: str = ""
    synonyms: tuple[str, ...] = ()

    @property
    def home(self) -> str | None:
        """SQL alias of the fact table a measure aggregates."""
        if self.kind != "MEASURE":
            return None
        return self.sql.split("{", 1)[1].split(".", 1)[0]


def attr(name, table, column, *, desc="", synonyms=()) -> Field:
    _, alias = TABLE_INFO[table]
    return Field(name, "ATTRIBUTE", StDef("attribute", column), f"{{{alias}.{column}}}",
                 table=table, column=column, description=desc, synonyms=synonyms)


def col_measure(name, table, column, agg="SUM", *, desc="", synonyms=()) -> Field:
    _, alias = TABLE_INFO[table]
    fn = {"SUM": "sum", "AVERAGE": "avg", "MIN": "min", "MAX": "max"}[agg]
    sql_fn = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX"}[agg]
    return Field(name, "MEASURE", StDef("fact_metric", column, fn, columns=(column,)),
                 f"{sql_fn}({{{alias}.{column}}})", table=table, column=column,
                 aggregation=agg, description=desc, synonyms=synonyms)


def formula(name, ts_expr, st: StDef, sql, *, level=(), desc="") -> Field:
    return Field(name, "MEASURE", st, sql, formula=ts_expr, level=level, description=desc)


@dataclass(frozen=True)
class Filter:
    field: str
    oper: str  # "in" | "not in"
    values: tuple[str, ...]


@dataclass(frozen=True)
class SemanticModel:
    """A ThoughtSpot Worksheet or Model."""

    name: str
    style: str  # worksheet | model
    tables: tuple[str, ...]
    joins: tuple[Join, ...]
    fields: tuple[Field, ...]
    filters: tuple[Filter, ...] = ()
    progressive: bool = True
    description: str = ""

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)

    def with_field(self, name: str, **changes) -> "SemanticModel":
        return replace(self, fields=tuple(replace(f, **changes) if f.name == name else f for f in self.fields))

    def without_field(self, name: str) -> "SemanticModel":
        return replace(self, fields=tuple(f for f in self.fields if f.name != name))

    def with_join_type(self, destination: str, join_type: str) -> "SemanticModel":
        return replace(self, joins=tuple(replace(j, type=join_type) if j.destination == destination else j
                                         for j in self.joins))


@dataclass(frozen=True)
class Viz:
    id: str
    name: str
    dims: tuple[str, ...]
    measures: tuple[str, ...]
    chart: str = "COLUMN"  # COLUMN | BAR | LINE | TABLE | KPI
    sort: tuple[str, str] | None = None  # (field, ASC|DESC)
    top_n: int | None = None
    title: str | None = None


@dataclass(frozen=True)
class Liveboard:
    name: str
    model: str
    visualizations: tuple[Viz, ...]
    filters: tuple[Filter, ...] = ()
    kind: str = "liveboard"  # liveboard | answer (answer = exactly one viz)

    def viz(self, viz_id: str) -> Viz:
        return next(v for v in self.visualizations if v.id == viz_id)


def _joins(*pairs: tuple[str, str]) -> tuple[Join, ...]:
    return tuple(j for j in TABLE_JOINS if (j.source, j.destination) in pairs)


# --------------------------------------------------------------------------------------------
# Sales Orders (Worksheet TML): order grain
# --------------------------------------------------------------------------------------------
SALES = SemanticModel(
    name="Sales Orders",
    style="worksheet",
    description="Order-level sales analysis by customer, store and date.",
    tables=("FACT_ORDERS", "DIM_CUSTOMER", "DIM_STORE", "DIM_DATE"),
    joins=_joins(("FACT_ORDERS", "DIM_CUSTOMER"), ("FACT_ORDERS", "DIM_STORE"), ("FACT_ORDERS", "DIM_DATE")),
    filters=(Filter("Order Status", "not in", ("Cancelled",)),),
    fields=(
        attr("Order ID", "FACT_ORDERS", "order_id"),
        attr("Order Date", "FACT_ORDERS", "order_date"),
        attr("Order Status", "FACT_ORDERS", "order_status"),
        attr("Customer ID", "FACT_ORDERS", "customer_id"),
        attr("Store ID", "FACT_ORDERS", "store_id"),
        attr("Customer Name", "DIM_CUSTOMER", "customer_name"),
        attr("Customer Segment", "DIM_CUSTOMER", "segment", synonyms=("Segment",)),
        attr("Customer Region", "DIM_CUSTOMER", "region"),
        attr("Store Name", "DIM_STORE", "store_name"),
        attr("Store City", "DIM_STORE", "city"),
        attr("Store Region", "DIM_STORE", "region"),
        attr("Store Type", "DIM_STORE", "store_type"),
        attr("Order Week", "DIM_DATE", "week_start_monday", desc="Week starting Monday"),
        attr("Order Month", "DIM_DATE", "month_start"),
        attr("Order Quarter", "DIM_DATE", "quarter"),
        attr("Order Year", "DIM_DATE", "year"),
        attr("Fiscal Year", "DIM_DATE", "fiscal_year", desc="Fiscal year starting in April"),
        col_measure("Revenue", "FACT_ORDERS", "order_amount", synonyms=("Sales", "Order Amount")),
        col_measure("Discount", "FACT_ORDERS", "discount_amount"),
        col_measure("Shipping", "FACT_ORDERS", "shipping_amount"),
        col_measure("Items Sold", "FACT_ORDERS", "item_count"),
        formula("Order Count", "unique count ( [{FACT_ORDERS}::Order ID] )",
                StDef("fact_metric", "order_id", "count", distinct=True, columns=("order_id",)),
                "COUNT(DISTINCT {o.order_id})"),
        formula("Customer Count", "unique count ( [{FACT_ORDERS}::Customer ID] )",
                StDef("fact_metric", "customer_id", "count", distinct=True, columns=("customer_id",)),
                "COUNT(DISTINCT {o.customer_id})"),
        formula("Avg Order Value",
                "sum ( [{FACT_ORDERS}::Order Amount] ) / unique count ( [{FACT_ORDERS}::Order ID] )",
                StDef("metric", "[Revenue] / [Order Count]"),
                "SUM({o.order_amount}) / NULLIF(COUNT(DISTINCT {o.order_id}), 0)"),
        formula("Discount Rate",
                "safe_divide ( sum ( [{FACT_ORDERS}::Discount Amount] ) , sum ( [{FACT_ORDERS}::Order Amount] ) )",
                StDef("metric", "If(([Revenue] = 0), 0, ([Discount] / [Revenue]))"),
                "CASE WHEN SUM({o.order_amount}) = 0 THEN 0 "
                "ELSE SUM({o.discount_amount}) / SUM({o.order_amount}) END",
                desc="Discount as a share of revenue; 0 when revenue is 0"),
        formula("Net Revenue",
                "sum ( [{FACT_ORDERS}::Order Amount] - ifnull ( [{FACT_ORDERS}::Discount Amount] , 0 ) )",
                StDef("fact_metric", 'ApplySimple("#0 - COALESCE(#1, 0)", order_amount, discount_amount)', "sum",
                      columns=("order_amount", "discount_amount")),
                "SUM({o.order_amount} - COALESCE({o.discount_amount}, 0))"),
        formula("Region Revenue",
                "group_aggregate ( sum ( [{FACT_ORDERS}::Order Amount] ) , { [{DIM_STORE}::Region] } , query_filters ( ) )",
                StDef("fact_metric", "order_amount", "sum", level=("Store Region",), columns=("order_amount",)),
                "SUM({o.order_amount})", level=("Store Region",),
                desc="Revenue of the whole store region, regardless of other grouping"),
    ),
)

SALES_OVERVIEW = Liveboard(
    name="Sales Overview",
    model="Sales Orders",
    filters=(Filter("Order Year", "in", ("2025",)),),
    visualizations=(
        Viz("V1", "Revenue by Store Region", ("Store Region",), ("Revenue",), "COLUMN", ("Revenue", "DESC")),
        Viz("V2", "Monthly Revenue Trend", ("Order Month",), ("Revenue", "Order Count"), "LINE",
            ("Order Month", "ASC")),
        Viz("V3", "Top Customers", ("Customer Name",), ("Revenue",), "TABLE", ("Revenue", "DESC"), top_n=10),
        Viz("V4", "Average Order Value", (), ("Avg Order Value",), "KPI"),
        Viz("V5", "Discount Rate by Status", ("Order Status",), ("Discount Rate", "Revenue"), "TABLE",
            ("Order Status", "ASC")),
        Viz("V6", "Weekly Net Revenue", ("Order Week",), ("Net Revenue", "Order Count"), "LINE",
            ("Order Week", "ASC")),
        Viz("V7", "Region vs Store Type", ("Store Region", "Store Type"), ("Revenue", "Region Revenue"), "TABLE",
            ("Store Region", "ASC")),
        Viz("V8", "Shipping by Customer Region", ("Customer Region",), ("Shipping", "Customer Count"), "COLUMN",
            ("Shipping", "DESC")),
    ),
)

SEGMENT_ANSWER = Liveboard(
    name="Revenue by Customer Segment",
    model="Sales Orders",
    kind="answer",
    visualizations=(
        Viz("A1", "Revenue by Customer Segment", ("Customer Segment",), ("Revenue", "Customer Count"), "BAR",
            ("Revenue", "DESC")),
    ),
)

# --------------------------------------------------------------------------------------------
# Order Lines (Model TML): line grain
# --------------------------------------------------------------------------------------------
LINES = SemanticModel(
    name="Order Lines",
    style="model",
    description="Line-level product performance.",
    tables=("FACT_ORDER_LINES", "FACT_ORDERS", "DIM_PRODUCT", "DIM_DATE"),
    joins=_joins(("FACT_ORDER_LINES", "FACT_ORDERS"), ("FACT_ORDER_LINES", "DIM_PRODUCT"),
                 ("FACT_ORDERS", "DIM_DATE")),
    fields=(
        attr("Order ID", "FACT_ORDERS", "order_id"),
        attr("Order Date", "FACT_ORDERS", "order_date"),
        attr("Order Status", "FACT_ORDERS", "order_status"),
        attr("Product ID", "FACT_ORDER_LINES", "product_id"),
        attr("Product Name", "DIM_PRODUCT", "product_name"),
        attr("Category", "DIM_PRODUCT", "category"),
        attr("Subcategory", "DIM_PRODUCT", "subcategory"),
        attr("Brand", "DIM_PRODUCT", "brand"),
        attr("Order Month", "DIM_DATE", "month_start"),
        attr("Order Year", "DIM_DATE", "year"),
        col_measure("Units", "FACT_ORDER_LINES", "quantity"),
        col_measure("Line Revenue", "FACT_ORDER_LINES", "line_amount"),
        col_measure("Line Cost", "FACT_ORDER_LINES", "line_cost"),
        formula("Gross Margin",
                "sum ( [{FACT_ORDER_LINES}::Line Amount] ) - sum ( [{FACT_ORDER_LINES}::Line Cost] )",
                StDef("metric", "[Line Revenue] - [Line Cost]"),
                "SUM({l.line_amount}) - SUM({l.line_cost})"),
        formula("Margin %",
                "( sum ( [{FACT_ORDER_LINES}::Line Amount] ) - sum ( [{FACT_ORDER_LINES}::Line Cost] ) ) "
                "/ sum ( [{FACT_ORDER_LINES}::Line Amount] )",
                StDef("metric", "([Line Revenue] - [Line Cost]) / [Line Revenue]"),
                "(SUM({l.line_amount}) - SUM({l.line_cost})) / NULLIF(SUM({l.line_amount}), 0)",
                desc="Margin as a share of line revenue (ratio of sums)"),
        formula("Avg Selling Price",
                "sum ( [{FACT_ORDER_LINES}::Line Amount] ) / sum ( [{FACT_ORDER_LINES}::Quantity] )",
                StDef("metric", "[Line Revenue] / [Units]"),
                "SUM({l.line_amount}) / NULLIF(SUM({l.quantity}), 0)"),
    ),
)

PRODUCT_PERFORMANCE = Liveboard(
    name="Product Performance",
    model="Order Lines",
    visualizations=(
        Viz("P1", "Units by Category", ("Category",), ("Units",), "COLUMN", ("Units", "DESC")),
        Viz("P2", "Margin by Category", ("Category",), ("Line Revenue", "Gross Margin", "Margin %"), "TABLE",
            ("Category", "ASC")),
        Viz("P3", "Top Products", ("Product Name",), ("Line Revenue",), "TABLE", ("Line Revenue", "DESC"),
            top_n=10),
        Viz("P4", "Monthly Units", ("Order Month",), ("Units", "Avg Selling Price"), "LINE",
            ("Order Month", "ASC")),
    ),
)

# --------------------------------------------------------------------------------------------
# Returns Analysis (Model TML): two facts (lines, returns) sharing DIM_PRODUCT
# --------------------------------------------------------------------------------------------
RETURNS = SemanticModel(
    name="Returns Analysis",
    style="model",
    description="Sales vs returns by product. Two facts at different grains share the product dimension.",
    tables=("FACT_ORDER_LINES", "FACT_RETURNS", "DIM_PRODUCT"),
    joins=_joins(("FACT_ORDER_LINES", "DIM_PRODUCT"), ("FACT_RETURNS", "DIM_PRODUCT")),
    fields=(
        attr("Product ID", "DIM_PRODUCT", "product_id"),
        attr("Category", "DIM_PRODUCT", "category"),
        attr("Subcategory", "DIM_PRODUCT", "subcategory"),
        attr("Brand", "DIM_PRODUCT", "brand"),
        col_measure("Line Revenue", "FACT_ORDER_LINES", "line_amount"),
        col_measure("Units Sold", "FACT_ORDER_LINES", "quantity"),
        col_measure("Refund Amount", "FACT_RETURNS", "refund_amount"),
        col_measure("Units Returned", "FACT_RETURNS", "return_qty"),
    ),
)

RETURNS_OVERVIEW = Liveboard(
    name="Returns Overview",
    model="Returns Analysis",
    visualizations=(
        Viz("R1", "Revenue vs Refunds by Category", ("Category",), ("Line Revenue", "Refund Amount"), "COLUMN",
            ("Category", "ASC")),
        Viz("R2", "Units Returned by Brand", ("Brand",), ("Units Sold", "Units Returned"), "TABLE",
            ("Units Returned", "DESC")),
    ),
)

MODELS = {m.name: m for m in (SALES, LINES, RETURNS)}
CONTENT = {
    "Sales Orders": (SALES_OVERVIEW, SEGMENT_ANSWER),
    "Order Lines": (PRODUCT_PERFORMANCE,),
    "Returns Analysis": (RETURNS_OVERVIEW,),
}
