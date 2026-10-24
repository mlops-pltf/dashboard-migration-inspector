"""sqlglot AST -> IR expressions."""

from __future__ import annotations

from collections.abc import Callable

import sqlglot
from sqlglot import exp

from inspector_contract import Agg, BinOp, Case, Expr, Func, Lit, Opaque, UnaryOp

DIALECT = "redshift"

# column resolver: (table alias or None, column name) -> Expr
ColumnResolver = Callable[[str | None, str], Expr]

_BINARY = {
    exp.Add: "+", exp.Sub: "-", exp.Mul: "*", exp.Div: "/", exp.Mod: "%",
    exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<=",
    exp.And: "and", exp.Or: "or", exp.DPipe: "||",
}
_AGG = {exp.Sum: "SUM", exp.Avg: "AVG", exp.Min: "MIN", exp.Max: "MAX", exp.Count: "COUNT",
        exp.Stddev: "STDDEV", exp.Variance: "VARIANCE", exp.Median: "MEDIAN"}


def parse_expression(text: str, dialect: str = DIALECT) -> exp.Expression:
    return sqlglot.parse_one(text, read=dialect)


def to_ir(node: exp.Expression, resolve: ColumnResolver) -> Expr:
    """Convert a scalar/aggregate SQL expression. Unknown functions keep their name as ``Func``."""
    if isinstance(node, exp.Paren):
        return to_ir(node.this, resolve)
    if isinstance(node, exp.Alias):
        return to_ir(node.this, resolve)
    if isinstance(node, exp.Column):
        if isinstance(node.this, exp.Star):
            return Opaque(dialect="sql", text=node.sql(dialect=DIALECT), reason="star")
        return resolve(node.table or None, node.name)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return Lit(value=node.this, type="string")
        text = node.this
        return Lit(value=float(text) if any(c in text for c in ".eE") else int(text), type="number")
    if isinstance(node, exp.Null):
        return Lit(value=None, type="null")
    if isinstance(node, exp.Boolean):
        return Lit(value=bool(node.this), type="boolean")
    for cls, op in _BINARY.items():
        if type(node) is cls:
            return BinOp(op=op, left=to_ir(node.left, resolve), right=to_ir(node.right, resolve))
    if isinstance(node, exp.Neg):
        inner = to_ir(node.this, resolve)
        if isinstance(inner, Lit) and inner.type == "number":
            return Lit(value=-inner.value, type="number")
        return UnaryOp(op="-", arg=inner)
    if isinstance(node, exp.Not):
        inner = node.this
        if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
            return UnaryOp(op="is_not_null", arg=to_ir(inner.this, resolve))
        return UnaryOp(op="not", arg=to_ir(inner, resolve))
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        return UnaryOp(op="is_null", arg=to_ir(node.this, resolve))
    if isinstance(node, exp.Case):
        whens = tuple((to_ir(i.this, resolve), to_ir(i.args["true"], resolve)) for i in node.args.get("ifs") or [])
        default = node.args.get("default")
        return Case(whens=whens, else_=to_ir(default, resolve) if default is not None else None)
    if isinstance(node, exp.If):
        false = node.args.get("false")
        return Case(whens=((to_ir(node.this, resolve), to_ir(node.args["true"], resolve)),),
                    else_=to_ir(false, resolve) if false is not None else None)
    if isinstance(node, exp.Coalesce):
        args = [node.this, *(node.expressions or [])]
        return Func(name="coalesce", args=tuple(to_ir(a, resolve) for a in args))
    if isinstance(node, exp.Nullif):
        return Func(name="nullif", args=(to_ir(node.this, resolve), to_ir(node.expression, resolve)))
    if isinstance(node, exp.In):
        return Func(name="in", args=(to_ir(node.this, resolve), *(to_ir(e, resolve) for e in node.expressions)))
    if isinstance(node, exp.Between):
        return Func(name="between", args=(to_ir(node.this, resolve), to_ir(node.args["low"], resolve),
                                           to_ir(node.args["high"], resolve)))
    if isinstance(node, exp.Cast):
        return Func(name="cast", args=(to_ir(node.this, resolve), Lit(value=node.to.sql(dialect=DIALECT).lower())))
    for cls, fn in _AGG.items():
        if isinstance(node, cls):
            arg = node.this
            distinct = isinstance(arg, exp.Distinct)
            if distinct:
                arg = arg.expressions[0]
            if arg is None or isinstance(arg, exp.Star):
                return Agg(fn=fn, arg=None, distinct=distinct)
            return Agg(fn=fn, arg=to_ir(arg, resolve), distinct=distinct)
    if isinstance(node, exp.Anonymous):
        return Func(name=str(node.this).lower(), args=tuple(to_ir(a, resolve) for a in node.expressions))
    if isinstance(node, exp.Func):
        args = [a for a in node.args.values() if isinstance(a, exp.Expression)]
        for v in node.args.values():
            if isinstance(v, list):
                args.extend(a for a in v if isinstance(a, exp.Expression))
        return Func(name=node.sql_name().lower(), args=tuple(to_ir(a, resolve) for a in args))
    return Opaque(dialect="sql", text=node.sql(dialect=DIALECT), reason=f"unsupported {type(node).__name__}")


def split_and(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, exp.Paren):
        return split_and(node.this)
    if isinstance(node, exp.And):
        return split_and(node.left) + split_and(node.right)
    return [node]
