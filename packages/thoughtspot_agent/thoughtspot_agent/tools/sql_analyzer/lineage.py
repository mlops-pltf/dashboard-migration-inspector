"""Lineage of a SELECT statement (Freeform SQL tables, ThoughtSpot SQL views, generated SQL).

For every output column: an expression over physical columns. Plus the joins (type, equi-join keys,
extra predicates), WHERE predicates, GROUP BY and DISTINCT. Subqueries and CTEs are flattened: their
sources and joins are reported with a ``outer.inner`` alias prefix.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError as SqlParseError

from inspector_contract import BinOp, Col, Expr, PCol, PhysicalColumn, PhysicalTable, SqlJoin, SqlLineage
from .convert import DIALECT, split_and, to_ir


class _Scope:
    def __init__(self):
        self.physical: dict[str, PhysicalTable] = {}  # alias -> table (this level)
        self.derived: dict[str, SqlLineage] = {}  # alias -> analysed subquery / CTE
        self.order: list[str] = []
        self.errors: list[str] = []

    def resolve(self, table: str | None, column: str) -> Expr:
        column = column.lower()
        if table is None:
            owners = [a for a in self.order if a in self.derived and column in self.derived[a].outputs]
            if len(owners) == 1:
                return self.derived[owners[0]].outputs[column]
            if len(self.order) == 1 and self.order[0] in self.physical:
                t = self.physical[self.order[0]]
                return PCol(column=PhysicalColumn(namespace=t.namespace, table=t.table, column=column))
            self.errors.append(f"cannot attribute unqualified column {column!r} to a source")
            return Col(table="?", column=column)
        alias = table.lower()
        if alias in self.physical:
            t = self.physical[alias]
            return PCol(column=PhysicalColumn(namespace=t.namespace, table=t.table, column=column))
        if alias in self.derived:
            sub = self.derived[alias]
            if column in sub.outputs:
                return sub.outputs[column]
            self.errors.append(f"{alias}.{column} is not an output of the subquery")
            return Col(table=alias, column=column)
        self.errors.append(f"unknown table alias {alias!r}")
        return Col(table=alias, column=column)


def _table(node: exp.Table, default_namespace: str) -> PhysicalTable:
    return PhysicalTable(namespace=(node.db or default_namespace).lower(), table=node.name.lower())


def _join_type(join: exp.Join) -> str:
    kind = (join.kind or "").upper()
    side = (join.side or "").upper()
    if kind == "CROSS":
        return "CROSS"
    if side in ("LEFT", "RIGHT", "FULL"):
        return side
    return "INNER"


def _analyze_select(select: exp.Select, ctes: dict[str, SqlLineage], default_namespace: str) -> SqlLineage:
    scope = _Scope()
    nested_joins: list[SqlJoin] = []
    nested_sources: dict[str, PhysicalTable] = {}

    def add_source(node: exp.Expression) -> str:
        alias = (node.alias_or_name or "").lower()
        if isinstance(node, exp.Table) and node.name.lower() in ctes and not node.db:
            sub = ctes[node.name.lower()]
            scope.derived[alias] = sub
        elif isinstance(node, exp.Table):
            scope.physical[alias] = _table(node, default_namespace)
        elif isinstance(node, exp.Subquery):
            inner = node.this
            sub = _analyze(inner, ctes, default_namespace) if isinstance(inner, (exp.Select, exp.Union)) \
                else SqlLineage(outputs={}, sources={}, errors=("unsupported subquery",))
            scope.derived[alias] = sub
        else:
            scope.errors.append(f"unsupported FROM item {type(node).__name__}")
        if alias in scope.derived:
            sub = scope.derived[alias]
            nested_sources.update({f"{alias}.{k}": v for k, v in sub.sources.items()})
            nested_joins.extend(j.model_copy(update={"left": tuple(f"{alias}.{x}" for x in j.left),
                                                     "right": f"{alias}.{j.right}"}) for j in sub.joins)
            scope.errors.extend(sub.errors)
        scope.order.append(alias)
        return alias

    from_ = select.args.get("from") or select.args.get("from_")
    root = add_source(from_.this) if from_ is not None else None

    joins: list[SqlJoin] = []
    for join in select.args.get("joins") or []:
        right = add_source(join.this)
        keys, extra = [], []
        for pred in split_and(join.args.get("on")):
            if isinstance(pred, exp.EQ) and isinstance(pred.left, exp.Column) and isinstance(pred.right, exp.Column):
                a = scope.resolve(pred.left.table or None, pred.left.name)
                b = scope.resolve(pred.right.table or None, pred.right.name)
                a_right = (pred.left.table or "").lower() == right
                if isinstance(a, PCol) and isinstance(b, PCol):
                    keys.append((b.column, a.column) if a_right else (a.column, b.column))
                    continue
            extra.append(to_ir(pred, scope.resolve))
        extra_expr = None
        for e in extra:
            extra_expr = e if extra_expr is None else BinOp(op="and", left=extra_expr, right=e)
        joins.append(SqlJoin(type=_join_type(join), left=tuple(a for a in scope.order if a != right),
                             right=right, keys=tuple(keys), extra=extra_expr))

    outputs: dict[str, Expr] = {}
    for proj in select.expressions:
        if isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)):
            target = proj.table.lower() if isinstance(proj, exp.Column) and proj.table else None
            for alias in ([target] if target else scope.order):
                if alias in scope.derived:
                    outputs.update(scope.derived[alias].outputs)
                else:
                    scope.errors.append(f"SELECT * over physical table {alias!r}: columns unknown without a catalog")
            continue
        name = (proj.alias_or_name or "").lower()
        outputs[name] = to_ir(proj, scope.resolve)

    where = tuple(to_ir(p, scope.resolve) for p in split_and(select.args.get("where").this)) \
        if select.args.get("where") else ()
    group = select.args.get("group")
    group_by = tuple(to_ir(g, scope.resolve) for g in group.expressions) if group else ()

    return SqlLineage(
        outputs=outputs,
        sources=dict(scope.physical) | nested_sources,
        root=root,
        joins=tuple(joins) + tuple(nested_joins),
        where=where,
        group_by=group_by,
        distinct=bool(select.args.get("distinct")),
        errors=tuple(dict.fromkeys(scope.errors)),
    )


def _analyze(node: exp.Expression, ctes: dict[str, SqlLineage], default_namespace: str) -> SqlLineage:
    with_ = node.args.get("with") or node.args.get("with_")
    if with_ is not None:
        ctes = dict(ctes)
        for cte in with_.expressions:
            ctes[cte.alias.lower()] = _analyze(cte.this, ctes, default_namespace)
    if isinstance(node, exp.Select):
        return _analyze_select(node, ctes, default_namespace)
    if isinstance(node, exp.Union):
        left = _analyze(node.left, ctes, default_namespace)
        return left.model_copy(update={"errors": left.errors + ("UNION: lineage taken from the first branch only",)})
    return SqlLineage(outputs={}, sources={}, errors=(f"not a SELECT: {type(node).__name__}",))


def analyze(sql: str, dialect: str = DIALECT, default_namespace: str = "public") -> SqlLineage:
    try:
        node = sqlglot.parse_one(sql, read=dialect)
    except SqlParseError as exc:
        return SqlLineage(outputs={}, sources={}, errors=(f"parse error: {exc}",))
    return _analyze(node, {}, default_namespace)
