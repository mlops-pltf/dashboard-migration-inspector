"""Strategy (MicroStrategy) expression language -> IR.

Handles metric expressions (``Sum<Distinct=True>([Order ID]){~+}``, ``If(([Revenue] = 0), 0, ...)``,
``NullToZero``, ``ApplySimple("#0 - COALESCE(#1, 0)", a, b)``), object references (``[Name]``,
``{Name}``, bare identifiers) and fact/attribute column expressions.

Level (dimensionality) suffix mapping:
  ``{~+}`` / ``{~}``        -> report level
  ``{~+, [A]}``             -> include A
  ``{[A]}`` / ``{A+}``      -> fixed at A
  ``{~+, ![A]}`` / ``{A-}`` -> exclude A
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlglot.errors import ParseError as SqlParseError

from inspector_contract import Agg, BinOp, Case, Expr, Func, Level, Lit, Opaque, Ref, UnaryOp
from ..sql_analyzer.convert import parse_expression, to_ir
from ..common.pratt import BINARY_PRECEDENCE, ParseError, Stream, Token

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<PFUNC>[A-Za-z_][A-Za-z0-9_]*<[^<>()]*>)(?=\s*\()
  | (?P<NUM>\d+(?:\.\d+)?)
  | (?P<STR>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<REF>\[[^\]]*\])
  | (?P<BRACE>\{[^{}]*\})
  | (?P<OP><>|!=|<=|>=|=|<|>|\+|-|\*|/)
  | (?P<LP>\()|(?P<RP>\))|(?P<COMMA>,)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
""", re.VERBOSE)

_AGGREGATES = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX", "stdev": "STDDEV",
               "stdevp": "STDDEV", "var": "VARIANCE", "varp": "VARIANCE", "median": "MEDIAN"}

# resolver(name) -> Expr ; called for [Name], {Name} and bare identifiers
Resolver = Callable[[str], Expr]


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


def _params(text: str) -> dict[str, str]:
    out = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip().lower()
    return out


def parse_level(text: str) -> Level:
    has_report, include, exclude = False, [], []
    for item in (i.strip() for i in text.strip("{}").split(",")):
        if not item:
            continue
        if item.startswith("~") or item == "^":
            has_report = True
            continue
        negative = item.startswith("!") or item.endswith("-")
        name = item.lstrip("!").rstrip("+-").strip().strip("[]{}").strip()
        (exclude if negative else include).append(name)
    if exclude:
        return Level(kind="exclude", dims=tuple(exclude))
    if include and has_report:
        return Level(kind="include", dims=tuple(include))
    if include:
        return Level(kind="fixed", dims=tuple(include))
    return Level(kind="report")


def apply_simple(template: str, args: list[Expr], resolve: Resolver) -> Expr:
    sql = re.sub(r"#(\d+)", lambda m: f"__arg{m.group(1)}__", template)
    try:
        node = parse_expression(sql)
    except SqlParseError as exc:
        return Opaque(dialect="strategy", text=template, reason=f"ApplySimple SQL: {exc}")

    def col(table: str | None, column: str) -> Expr:
        m = re.fullmatch(r"__arg(\d+)__", column)
        return args[int(m.group(1))] if m else resolve(column)

    return to_ir(node, col)


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
            left = BinOp(op="!=" if op == "<>" else op, left=left, right=self.expr(prec + 1))

    def unary(self) -> Expr:
        if self.s.accept("IDENT", "not"):
            return UnaryOp(op="not", arg=self.expr(BINARY_PRECEDENCE["="]))  # NOT binds looser than comparisons
        if self.s.at("OP", "-"):
            self.s.next()
            inner = self.unary()
            if isinstance(inner, Lit) and inner.type == "number":
                return Lit(value=-inner.value, type="number")
            return UnaryOp(op="-", arg=inner)
        return self.postfix(self.primary())

    def postfix(self, e: Expr) -> Expr:
        s = self.s
        if isinstance(e, Agg) and s.at("BRACE"):
            e = e.model_copy(update={"level": parse_level(s.next().text)})
        if isinstance(e, Agg) and s.at("OP", "<") and s.at("REF", k=1) and s.at("OP", ">", k=2):
            s.next()
            cond = self.resolve(s.next().text[1:-1].strip())
            s.next()
            e = e.model_copy(update={"condition": cond})
        return e

    def primary(self) -> Expr:
        s = self.s
        t = s.peek()
        if t.kind == "NUM":
            s.next()
            return Lit(value=float(t.text) if "." in t.text else int(t.text), type="number")
        if t.kind == "STR":
            s.next()
            return Lit(value=t.text[1:-1], type="string")
        if t.kind in ("REF", "BRACE"):
            s.next()
            return self.resolve(t.text[1:-1].strip())
        if t.kind == "LP":
            s.next()
            e = self.expr(0)
            s.expect("RP")
            return e
        if t.kind == "PFUNC":
            s.next()
            name, params = t.text.split("<", 1)
            return self.call(name, _params(params[:-1]))
        if t.kind == "IDENT":
            s.next()
            low = t.text.lower()
            if s.at("LP"):
                return self.call(t.text, {})
            if low in ("true", "false"):
                return Lit(value=low == "true", type="boolean")
            if low == "null":
                return Lit(value=None, type="null")
            return self.resolve(t.text)
        raise ParseError(f"unexpected {t.text!r} at {t.pos}")

    def call(self, name: str, params: dict[str, str]) -> Expr:
        s = self.s
        s.expect("LP")
        args: list[Expr] = []
        while not s.at("RP"):
            args.append(self.expr(0))
            if not s.accept("COMMA"):
                break
        s.expect("RP")
        low = name.lower()
        if low in _AGGREGATES:
            return Agg(fn=_AGGREGATES[low], arg=args[0] if args else None, distinct=params.get("distinct") == "true")
        if low == "nulltozero":
            return Func(name="coalesce", args=(args[0], Lit(value=0, type="number")))
        if low == "zerotonull":
            return Func(name="nullif", args=(args[0], Lit(value=0, type="number")))
        if low == "if":
            return Case(whens=((args[0], args[1]),), else_=args[2] if len(args) > 2 else None)
        if low == "case":
            pairs = [(args[i], args[i + 1]) for i in range(0, len(args) - 1, 2)]
            return Case(whens=tuple(pairs), else_=args[-1] if len(args) % 2 else None)
        if low in ("isnull", "isnotnull"):
            return UnaryOp(op="is_null" if low == "isnull" else "is_not_null", arg=args[0])
        if low in ("applysimple", "applyagg"):
            template = args[0]
            if not (isinstance(template, Lit) and template.type == "string"):
                raise ParseError("ApplySimple needs a string template")
            return apply_simple(template.value, args[1:], self.resolve)
        return Func(name=low, args=tuple(args))


def parse(text: str, resolver: Resolver | None = None) -> Expr:
    """Parse a Strategy expression. Never raises: failures become ``Opaque``."""
    try:
        return _Parser(text, resolver or (lambda n: Ref(name=n))).parse()
    except ParseError as exc:
        return Opaque(dialect="strategy", text=text, reason=str(exc))
