"""ThoughtSpot formula language -> IR.

Covers the aggregate, conditional, group_* and *_if families, safe_divide/ifnull, if-then-else and
column references ``[TABLE_PATH::Column]`` / ``[Column]``. Unknown functions become ``Func`` nodes
(their name is kept); text that cannot be parsed becomes ``Opaque``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from inspector_contract import Agg, BinOp, Case, Col, Expr, Func, Level, Lit, Opaque, Ref, UnaryOp
from ..common.pratt import BINARY_PRECEDENCE, ParseError, Stream, Token

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<NUM>\d+(?:\.\d+)?)
  | (?P<STR>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<REF>\[[^\]]*\])
  | (?P<OP>!=|<>|<=|>=|=|<|>|\+|-|\*|/|%)
  | (?P<LP>\()|(?P<RP>\))|(?P<LB>\{)|(?P<RB>\})|(?P<COMMA>,)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
""", re.VERBOSE)

_KEYWORDS = {"and", "or", "not", "if", "then", "else", "true", "false", "null"}

AGGREGATES = {"sum": "SUM", "average": "AVG", "avg": "AVG", "count": "COUNT", "unique count": "COUNT_DISTINCT",
              "unique_count": "COUNT_DISTINCT", "min": "MIN", "max": "MAX", "stddev": "STDDEV",
              "variance": "VARIANCE", "median": "MEDIAN"}
CONDITIONAL = {"sum_if": "SUM", "count_if": "COUNT", "unique_count_if": "COUNT_DISTINCT", "average_if": "AVG",
               "max_if": "MAX", "min_if": "MIN", "stddev_if": "STDDEV", "variance_if": "VARIANCE"}
GROUP = {"group_sum": "SUM", "group_count": "COUNT", "group_unique_count": "COUNT_DISTINCT", "group_average": "AVG",
         "group_max": "MAX", "group_min": "MIN", "group_stddev": "STDDEV", "group_variance": "VARIANCE"}
RENAMES = {"ifnull": "coalesce"}

# resolver(table_path_or_None, column_name) -> Col | Ref
Resolver = Callable[[str | None, str], Expr]


def _default_resolver(table: str | None, column: str) -> Expr:
    return Col(table=table, column=column) if table else Ref(name=column)


def tokenize(text: str) -> list[Token]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ParseError(f"unexpected character {text[pos]!r} at {pos}")
        kind = m.lastgroup
        if kind != "ws":
            tok = m.group(kind)
            if kind == "IDENT" and tok.lower() in ("and", "or"):
                kind = "OP"
            out.append(Token(kind, tok, pos))
        pos = m.end()
    out.append(Token("EOF", "", pos))
    return out


def _agg(fn: str, arg: Expr, condition: Expr | None = None, level: Level | None = None) -> Agg:
    distinct = fn == "COUNT_DISTINCT"
    return Agg(fn="COUNT" if distinct else fn, arg=arg, distinct=distinct, condition=condition, level=level)


def _dims(e: Expr) -> list[str]:
    """Names referenced in a group set: {[A], [B]} or query_groups() +/- {...}."""
    if isinstance(e, Func) and e.name == "set":
        return [_dim_name(a) for a in e.args]
    return []


def _dim_name(e: Expr) -> str:
    if isinstance(e, Col):
        return f"{e.table}::{e.column}"
    if isinstance(e, Ref):
        return e.name
    raise ParseError("group dimensions must be column references")


def _level(groups: Expr, filters: Expr | None) -> Level:
    filt = "apply"
    if filters is not None and isinstance(filters, Func) and filters.name == "set" and not filters.args:
        filt = "ignore"
    if isinstance(groups, Func) and groups.name == "query_groups":
        return Level(kind="report", filters=filt)
    if isinstance(groups, BinOp) and isinstance(groups.left, Func) and groups.left.name == "query_groups":
        return Level(kind="include" if groups.op == "+" else "exclude", dims=tuple(_dims(groups.right)), filters=filt)
    return Level(kind="fixed", dims=tuple(_dims(groups)), filters=filt)


class _Parser:
    def __init__(self, text: str, resolver: Resolver):
        self.s = Stream(tokenize(text), text)
        self.resolve = resolver

    def parse(self) -> Expr:
        e = self.expr(0)
        self.s.expect("EOF")
        return e

    def expr(self, min_prec: int) -> Expr:
        left = self.unary()
        while True:
            t = self.s.peek()
            op = t.text.lower()
            prec = BINARY_PRECEDENCE.get(op) if t.kind == "OP" else None
            if prec is None or prec < min_prec:
                return left
            self.s.next()
            right = self.expr(prec + 1)
            left = BinOp(op="!=" if op == "<>" else op, left=left, right=right)

    def unary(self) -> Expr:
        if self.s.accept("IDENT", "not"):
            return UnaryOp(op="not", arg=self.expr(BINARY_PRECEDENCE["="]))  # NOT binds looser than comparisons
        if self.s.at("OP", "-"):
            self.s.next()
            inner = self.unary()
            if isinstance(inner, Lit) and inner.type == "number":
                return Lit(value=-inner.value, type="number")
            return UnaryOp(op="-", arg=inner)
        return self.primary()

    def primary(self) -> Expr:
        s = self.s
        t = s.peek()
        if t.kind == "NUM":
            s.next()
            return Lit(value=float(t.text) if "." in t.text else int(t.text), type="number")
        if t.kind == "STR":
            s.next()
            return Lit(value=t.text[1:-1].replace("\\'", "'").replace('\\"', '"'), type="string")
        if t.kind == "REF":
            s.next()
            inner = t.text[1:-1].strip()
            table, _, column = inner.rpartition("::")
            return self.resolve(table.strip() or None, column.strip())
        if t.kind == "LP":
            s.next()
            e = self.expr(0)
            s.expect("RP")
            return e
        if t.kind == "LB":
            s.next()
            items = []
            while not s.at("RB"):
                items.append(self.expr(0))
                if not s.accept("COMMA"):
                    break
            s.expect("RB")
            return Func(name="set", args=tuple(items))
        if t.kind == "IDENT":
            low = t.text.lower()
            if low == "if":
                return self.if_expr()
            if low in ("true", "false"):
                s.next()
                return Lit(value=low == "true", type="boolean")
            if low == "null":
                s.next()
                return Lit(value=None, type="null")
            words = []
            while s.at("IDENT") and s.peek().text.lower() not in _KEYWORDS:
                words.append(s.next().text.lower())
            if words and s.at("LP"):
                return self.call(" ".join(words))
            raise ParseError(f"unexpected {' '.join(words) or t.text!r} at {t.pos}")
        raise ParseError(f"unexpected {t.text!r} at {t.pos}")

    def if_expr(self) -> Expr:
        s = self.s
        s.expect("IDENT", "if")
        whens = []
        cond = self.expr(0)
        s.expect("IDENT", "then")
        whens.append((cond, self.expr(0)))
        else_ = None
        while s.accept("IDENT", "else"):
            if s.accept("IDENT", "if"):
                cond = self.expr(0)
                s.expect("IDENT", "then")
                whens.append((cond, self.expr(0)))
                continue
            else_ = self.expr(0)
            break
        return Case(whens=tuple(whens), else_=else_)

    def call(self, name: str) -> Expr:
        s = self.s
        s.expect("LP")
        args: list[Expr] = []
        while not s.at("RP"):
            args.append(self.expr(0))
            if not s.accept("COMMA"):
                break
        s.expect("RP")
        key = name.replace(" ", "_") if name not in AGGREGATES else name

        if name in AGGREGATES:
            return _agg(AGGREGATES[name], args[0] if args else None)
        if key in CONDITIONAL:
            return _agg(CONDITIONAL[key], args[1], condition=args[0])
        if key in GROUP:
            dims = []
            for a in args[1:]:
                dims.extend(_dims(a) if isinstance(a, Func) and a.name == "set" else [_dim_name(a)])
            return _agg(GROUP[key], args[0], level=Level(kind="fixed", dims=tuple(dims)))
        if key == "group_aggregate":
            inner = args[0]
            if not isinstance(inner, Agg):
                raise ParseError("group_aggregate needs an aggregate as its first argument")
            return inner.model_copy(update={"level": _level(args[1], args[2] if len(args) > 2 else None)})
        if key == "safe_divide" and len(args) == 2:
            # ThoughtSpot semantics made explicit: 0 (not NULL) when the divisor is 0
            zero = Lit(value=0, type="number")
            return Case(whens=((BinOp(op="=", left=args[1], right=zero), zero),),
                        else_=BinOp(op="/", left=args[0], right=args[1]))
        if key == "isnull":
            return UnaryOp(op="is_null", arg=args[0])
        if key == "isnotnull":
            return UnaryOp(op="is_not_null", arg=args[0])
        return Func(name=RENAMES.get(key, key), args=tuple(args))


def parse(text: str, resolver: Resolver | None = None) -> Expr:
    """Parse a ThoughtSpot formula. Never raises: failures become ``Opaque``."""
    try:
        return _Parser(text, resolver or _default_resolver).parse()
    except ParseError as exc:
        return Opaque(dialect="thoughtspot", text=text, reason=str(exc))
