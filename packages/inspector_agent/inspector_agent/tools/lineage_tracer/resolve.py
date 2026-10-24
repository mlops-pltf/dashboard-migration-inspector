"""Resolve every field of a semantic model down to physical warehouse columns.

* ``Col(table, column)`` -> the logical table's relation: a physical column, or the SQL lineage of a
  Freeform SQL / SQL view output column.
* ``Ref(name)`` -> the referenced field's own resolved expression (Strategy metrics referencing fact
  metrics, ThoughtSpot formulas referencing worksheet columns), with cycle detection.
* Level dimensions (``group_aggregate`` / dimensionality) -> physical column names, so ThoughtSpot
  ``DIM_STORE_1::Region`` and Strategy ``Store Region`` compare equal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from inspector_contract import Agg, Col, Expr, Opaque, PCol, PhysicalColumn, Ref, SemanticModel, transform, unresolved
from inspector_contract import columns as expr_columns


class Resolved(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    kind: str
    expr: Expr
    columns: tuple[PhysicalColumn, ...]
    tables: tuple[str, ...]  # logical tables the definition reads
    status: Literal["exact", "partial"]
    # attributes that are join keys live on several tables (fact_orders.order_date = dim_date.date_key):
    # every physical column the attribute can be read from
    equivalent_columns: tuple[PhysicalColumn, ...] = ()
    issues: tuple[str, ...] = ()


class _Resolver:
    def __init__(self, model: SemanticModel):
        self.m = model
        self.cache: dict[str, tuple[Expr, tuple[str, ...], tuple[str, ...]]] = {}

    def column(self, col: Col, tables: list[str], issues: list[str]) -> Expr:
        t = self.m.table(col.table)
        if t is None:
            issues.append(f"unknown table {col.table!r}")
            return col
        if col.table not in tables:
            tables.append(col.table)
        cdef = t.column(col.column)
        physical = cdef.physical if cdef else col.column.lower()
        rel = t.relation
        if rel.kind == "physical" and rel.table is not None:
            if cdef is None and t.columns:
                issues.append(f"column {col.column!r} not declared on {t.name}")
            return PCol(column=PhysicalColumn(namespace=rel.table.namespace, table=rel.table.table, column=physical))
        if rel.kind == "sql" and rel.lineage is not None:
            out = rel.lineage.outputs.get(physical)
            if out is None:
                issues.append(f"{physical!r} is not an output of the SQL of {t.name}")
                return Opaque(dialect="sql", text=f"{t.name}.{physical}", reason="not an SQL output column")
            return out
        issues.append(f"table {t.name} has no usable relation")
        return col

    def dim(self, name: str, tables: list[str], issues: list[str]) -> str:
        table, sep, column = name.rpartition("::")
        if sep:
            target = self.column(Col(table=table, column=column), tables, issues)
        else:
            f = self.m.field(name)
            target = self.field(f.name)[0] if f else None
        if isinstance(target, PCol):
            return str(target.column)
        issues.append(f"level dimension {name!r} does not resolve to a single column")
        return name

    def expr(self, e: Expr, stack: tuple[str, ...], tables: list[str], issues: list[str]) -> Expr:
        def fn(node: Expr) -> Expr | None:
            if isinstance(node, Col):
                return self.column(node, tables, issues)
            if isinstance(node, Ref):
                f = self.m.field(node.name)
                if f is None:
                    issues.append(f"unknown reference [{node.name}]")
                    return None
                if f.name in stack:
                    issues.append(f"circular reference via [{node.name}]")
                    return None
                sub, sub_tables, sub_issues = self.field(f.name, stack)
                tables.extend(t for t in sub_tables if t not in tables)
                issues.extend(sub_issues)
                return sub
            if isinstance(node, Agg) and node.level is not None and node.level.dims:
                dims = tuple(self.dim(d, tables, issues) for d in node.level.dims)
                return node.model_copy(update={"level": node.level.model_copy(update={"dims": dims})})
            return None

        return transform(e, fn)

    def field(self, name: str, stack: tuple[str, ...] = ()) -> tuple[Expr, tuple[str, ...], tuple[str, ...]]:
        if name in self.cache:
            return self.cache[name]
        f = self.m.field(name)
        tables: list[str] = []
        issues: list[str] = []
        out = self.expr(f.expr, stack + (f.name,), tables, issues)
        result = (out, tuple(tables), tuple(dict.fromkeys(issues)))
        self.cache[name] = result
        return result


def resolve_model(model: SemanticModel) -> dict[str, Resolved]:
    r = _Resolver(model)
    out = {}
    for f in model.fields:
        expr, tables, issues = r.field(f.name)
        left = unresolved(expr)
        equivalents = list(expr_columns(expr))
        if f.kind == "ATTRIBUTE":
            for p in f.placements:
                for c in expr_columns(r.expr(p.expr, (f.name,), [], [])):
                    if c not in equivalents:
                        equivalents.append(c)
        out[f.name] = Resolved(field=f.name, kind=f.kind, expr=expr, columns=tuple(expr_columns(expr)),
                               tables=tables, status="partial" if left else "exact",
                               equivalent_columns=tuple(equivalents),
                               issues=issues + tuple(f"unresolved {type(n).__name__}: {n}" for n in left
                                                     if not isinstance(n, Opaque) or n.reason not in " ".join(issues)))
    return out


def resolve_expr(model: SemanticModel, expr: Expr) -> tuple[Expr, tuple[str, ...]]:
    """Resolve an arbitrary expression in the context of ``model`` (e.g. a filter or an attribute placement)."""
    r = _Resolver(model)
    tables: list[str] = []
    issues: list[str] = []
    return r.expr(expr, (), tables, issues), tuple(issues)


def home_table(r: Resolved):
    """The physical table a measure aggregates: the first column read inside an aggregate."""
    from inspector_contract import PCol, walk

    for node in walk(r.expr):
        if isinstance(node, Agg) and node.arg is not None:
            for inner in walk(node.arg):
                if isinstance(inner, PCol):
                    return inner.column.physical_table
    return r.columns[0].physical_table if r.columns else None
