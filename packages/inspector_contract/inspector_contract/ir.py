"""Intermediate representation (IR): the language the agents exchange.

Experts translate their tool's metadata into it; the Inspector only ever reads IR.

Every object that comes from a source file keeps a ``SourceRef`` so findings can point at the exact
place on each side. All models are immutable.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field as PField


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRef(_Base):
    file: str
    path: str = ""  # YAML/JSON path, e.g. worksheet.formulas[3]
    line: int | None = None
    object_id: str | None = None

    def __str__(self) -> str:
        where = f":{self.line}" if self.line else (f"#{self.path}" if self.path else "")
        return f"{self.file}{where}"


# ---------------------------------------------------------------------------------------------
# Physical layer
# ---------------------------------------------------------------------------------------------
class PhysicalTable(_Base):
    """A warehouse table. Identity is (namespace, table), case-folded; the database is informational."""

    namespace: str
    table: str

    def __str__(self) -> str:
        return f"{self.namespace}.{self.table}"


class PhysicalColumn(_Base):
    namespace: str
    table: str
    column: str

    @property
    def physical_table(self) -> PhysicalTable:
        return PhysicalTable(namespace=self.namespace, table=self.table)

    def __str__(self) -> str:
        return f"{self.namespace}.{self.table}.{self.column}"


# ---------------------------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------------------------
class Level(_Base):
    """Aggregation level. ``report`` = the query's grain."""

    kind: Literal["report", "fixed", "include", "exclude"] = "report"
    dims: tuple[str, ...] = ()
    filters: Literal["apply", "ignore"] = "apply"


class Col(_Base):
    """A column of a logical table (ThoughtSpot table / table path, Strategy logical table)."""

    node: Literal["col"] = "col"
    table: str
    column: str


class PCol(_Base):
    """A resolved physical column."""

    node: Literal["pcol"] = "pcol"
    column: PhysicalColumn


class Ref(_Base):
    """A reference to another semantic object by name (worksheet column, Strategy metric/attribute)."""

    node: Literal["ref"] = "ref"
    name: str


class Lit(_Base):
    node: Literal["lit"] = "lit"
    value: str | int | float | bool | None
    type: Literal["string", "number", "boolean", "null"] = "string"


class Func(_Base):
    node: Literal["func"] = "func"
    name: str  # normalised lower-case name, e.g. coalesce, safe_divide, date_trunc
    args: tuple["Expr", ...] = ()


class Agg(_Base):
    node: Literal["agg"] = "agg"
    fn: str  # SUM | COUNT | AVG | MIN | MAX | STDDEV | VARIANCE | MEDIAN | ...
    arg: "Expr | None" = None  # None for COUNT(*)
    distinct: bool = False
    condition: "Expr | None" = None  # sum_if / conditional metric
    level: Level | None = None  # None = report level


class BinOp(_Base):
    node: Literal["binop"] = "binop"
    op: str  # + - * / % = != < <= > >= and or ||
    left: "Expr"
    right: "Expr"


class UnaryOp(_Base):
    node: Literal["unary"] = "unary"
    op: str  # - not is_null is_not_null
    arg: "Expr"


class Case(_Base):
    node: Literal["case"] = "case"
    whens: tuple[tuple["Expr", "Expr"], ...]
    else_: "Expr | None" = None


class Opaque(_Base):
    """Something we could not parse or do not understand. Never provably equal to anything."""

    node: Literal["opaque"] = "opaque"
    dialect: str
    text: str
    reason: str = ""


Expr = Annotated[Union[Col, PCol, Ref, Lit, Func, Agg, BinOp, UnaryOp, Case, Opaque], PField(discriminator="node")]

for _m in (Func, Agg, BinOp, UnaryOp, Case):
    _m.model_rebuild()


# ---------------------------------------------------------------------------------------------
# SQL analysis (Freeform SQL tables, ThoughtSpot SQL views)
# ---------------------------------------------------------------------------------------------
JoinType = Literal["INNER", "LEFT", "RIGHT", "FULL", "CROSS", "UNKNOWN"]


class SqlJoin(_Base):
    type: JoinType
    left: tuple[str, ...]  # aliases already in the FROM clause
    right: str  # alias being joined
    keys: tuple[tuple[PhysicalColumn, PhysicalColumn], ...] = ()  # (left side, right side) equi-join pairs
    extra: Expr | None = None  # non equi-join part of the ON condition


class SqlLineage(_Base):
    outputs: dict[str, Expr]  # output column (lower case) -> expression over PCol
    sources: dict[str, PhysicalTable]  # alias -> physical table (derived sources are flattened)
    root: str | None = None  # alias of the first FROM source
    joins: tuple[SqlJoin, ...] = ()
    where: tuple[Expr, ...] = ()  # AND-split predicates
    group_by: tuple[Expr, ...] = ()
    distinct: bool = False
    errors: tuple[str, ...] = ()


class Relation(_Base):
    kind: Literal["physical", "sql"]
    table: PhysicalTable | None = None
    database: str | None = None
    sql: str | None = None
    lineage: SqlLineage | None = None


# ---------------------------------------------------------------------------------------------
# Semantic layer
# ---------------------------------------------------------------------------------------------
class ColumnDef(_Base):
    name: str  # name used by expressions (ThoughtSpot display name / Strategy column name)
    physical: str  # column of the relation (lower case)
    data_type: str | None = None


class LogicalTable(_Base):
    id: str
    name: str
    relation: Relation
    columns: tuple[ColumnDef, ...] = ()
    source: SourceRef | None = None

    def column(self, name: str) -> ColumnDef | None:
        key = name.lower()
        return next((c for c in self.columns if c.name.lower() == key or c.physical == key), None)


Cardinality = Literal["N:1", "1:N", "1:1", "N:M", "unknown"]


class Join(_Base):
    id: str
    left: str  # logical table id (many side when cardinality is N:1)
    right: str
    type: JoinType
    keys: tuple[tuple[str, str], ...]  # (left column name, right column name)
    cardinality: Cardinality = "unknown"
    origin: Literal["declared", "derived", "sql"] = "declared"
    # when_needed: joined only if a query uses the other table (ThoughtSpot progressive joins, Strategy attribute
    # joins); always: applied to every query (non-progressive worksheets, joins inside one SQL)
    applied: Literal["when_needed", "always"] = "when_needed"
    source: SourceRef | None = None


class Placement(_Base):
    """Where a Strategy attribute form lives: one expression per logical table."""

    table: str
    expr: Expr


class Field(_Base):
    id: str
    name: str
    kind: Literal["ATTRIBUTE", "MEASURE"]
    expr: Expr
    object_type: str  # ts:column | ts:formula | mstr:attribute | mstr:fact_metric | mstr:metric | ossie:...
    data_type: str | None = None
    description: str = ""
    synonyms: tuple[str, ...] = ()
    hidden: bool = False
    placements: tuple[Placement, ...] = ()
    provenance: Literal["parsed", "interpreted"] = "parsed"  # interpreted = read by an LLM, not a parser
    source: SourceRef | None = None


class ModelFilter(_Base):
    expr: Expr
    source: SourceRef | None = None


class Relationship(_Base):
    parent: str
    child: str
    table: str
    type: str


class SemanticModel(_Base):
    id: str
    name: str
    tool: Literal["thoughtspot", "strategy"]
    kind: str  # worksheet | model | sql_view | mosaic | ossie
    tables: tuple[LogicalTable, ...] = ()
    joins: tuple[Join, ...] = ()
    fields: tuple[Field, ...] = ()
    filters: tuple[ModelFilter, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    settings: dict[str, Any] = {}
    lossy: tuple[str, ...] = ()  # information the input format cannot carry
    description: str = ""
    source: SourceRef | None = None

    def table(self, table_id: str) -> LogicalTable | None:
        return next((t for t in self.tables if t.id == table_id), None)

    def field(self, name: str) -> Field | None:
        key = name.lower()
        return next((f for f in self.fields if f.name.lower() == key), None)


# ---------------------------------------------------------------------------------------------
# Content layer
# ---------------------------------------------------------------------------------------------
class FieldUse(_Base):
    field: str
    bucket: str | None = None  # date bucket, e.g. monthly
    aggregation: str | None = None  # aggregation override from the search query


class FilterSpec(_Base):
    field: str
    op: str  # in | not in | = | != | < | <= | > | >= | between | relative
    values: tuple[str, ...] = ()


class QuerySpec(_Base):
    dimensions: tuple[FieldUse, ...] = ()
    measures: tuple[FieldUse, ...] = ()
    filters: tuple[FilterSpec, ...] = ()
    sort: tuple[tuple[str, Literal["ASC", "DESC"]], ...] = ()
    top_n: int | None = None
    unparsed: tuple[str, ...] = ()


class Visualization(_Base):
    id: str
    name: str
    query: QuerySpec
    chart_type: str  # normalised: column | bar | line | table | kpi | pie | area | other
    source: SourceRef | None = None


class DashboardFilter(_Base):
    field: str
    op: str
    values: tuple[str, ...]
    scope: tuple[str, ...] = ()  # visualization ids; empty = all


class Dashboard(_Base):
    id: str
    name: str
    tool: Literal["thoughtspot", "strategy"]
    kind: Literal["liveboard", "answer", "dashboard"]
    models: tuple[str, ...]
    visualizations: tuple[Visualization, ...]
    filters: tuple[DashboardFilter, ...] = ()
    fields: tuple[Field, ...] = ()  # defined in the dashboard itself (Strategy derived metrics), used by name
    source: SourceRef | None = None


# ---------------------------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------------------------
class ParseIssue(_Base):
    severity: Literal["error", "warning"]
    message: str
    source: SourceRef | None = None


class MappingEntry(_Base):
    thoughtspot: str
    strategy: str


class IgnoreEntry(_Base):
    thoughtspot: str
    reason: str = ""


class Mapping(_Base):
    models: tuple[MappingEntry, ...] = ()
    fields: tuple[MappingEntry, ...] = ()
    visualizations: tuple[MappingEntry, ...] = ()
    ignore: tuple[IgnoreEntry, ...] = ()


class Side(_Base):
    models: tuple[SemanticModel, ...] = ()
    dashboards: tuple[Dashboard, ...] = ()


class MigrationPackage(_Base):
    id: str
    root: str
    thoughtspot: Side
    strategy: Side
    mapping: Mapping | None = None
    strategy_format: str | None = None
    issues: tuple[ParseIssue, ...] = ()

    @property
    def errors(self) -> tuple[ParseIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")
