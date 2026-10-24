"""Walking, transforming and printing expressions."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from .ir import Agg, BinOp, Case, Col, Expr, Func, Lit, Opaque, PCol, Ref, UnaryOp


def children(e: Expr) -> list[Expr]:
    if isinstance(e, Func):
        return list(e.args)
    if isinstance(e, Agg):
        return [x for x in (e.arg, e.condition) if x is not None]
    if isinstance(e, BinOp):
        return [e.left, e.right]
    if isinstance(e, UnaryOp):
        return [e.arg]
    if isinstance(e, Case):
        out = [x for pair in e.whens for x in pair]
        return out + ([e.else_] if e.else_ is not None else [])
    return []


def walk(e: Expr) -> Iterator[Expr]:
    yield e
    for c in children(e):
        yield from walk(c)


def transform(e: Expr, fn: Callable[[Expr], Expr | None]) -> Expr:
    """Bottom-up rewrite. ``fn`` returns a replacement or None to keep the (rebuilt) node."""
    if isinstance(e, Func):
        e = e.model_copy(update={"args": tuple(transform(a, fn) for a in e.args)})
    elif isinstance(e, Agg):
        e = e.model_copy(update={"arg": transform(e.arg, fn) if e.arg is not None else None,
                                 "condition": transform(e.condition, fn) if e.condition is not None else None})
    elif isinstance(e, BinOp):
        e = e.model_copy(update={"left": transform(e.left, fn), "right": transform(e.right, fn)})
    elif isinstance(e, UnaryOp):
        e = e.model_copy(update={"arg": transform(e.arg, fn)})
    elif isinstance(e, Case):
        e = e.model_copy(update={"whens": tuple((transform(c, fn), transform(v, fn)) for c, v in e.whens),
                                 "else_": transform(e.else_, fn) if e.else_ is not None else None})
    out = fn(e)
    return e if out is None else out


def columns(e: Expr) -> list:
    """Physical columns referenced (PCol leaves), in first-seen order."""
    seen = []
    for node in walk(e):
        if isinstance(node, PCol) and node.column not in seen:
            seen.append(node.column)
    return seen


def unresolved(e: Expr) -> list[Expr]:
    return [n for n in walk(e) if isinstance(n, (Col, Ref, Opaque))]


def has_aggregate(e: Expr) -> bool:
    return any(isinstance(n, Agg) for n in walk(e))


_PREC = {"or": 1, "and": 2, "=": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3, "+": 4, "-": 4, "||": 4,
         "*": 5, "/": 5, "%": 5}


def to_text(e: Expr, parent: int = 0) -> str:
    """Compact, SQL-like rendering for reports and LLM prompts."""
    if isinstance(e, PCol):
        return str(e.column)
    if isinstance(e, Col):
        return f"[{e.table}::{e.column}]"
    if isinstance(e, Ref):
        return f"[{e.name}]"
    if isinstance(e, Lit):
        if e.type == "string":
            return "'" + str(e.value).replace("'", "''") + "'"
        return "NULL" if e.value is None else str(e.value).upper() if isinstance(e.value, bool) else str(e.value)
    if isinstance(e, Func):
        if e.name == "in" and e.args:
            return f"{to_text(e.args[0], 6)} IN ({', '.join(to_text(a) for a in e.args[1:])})"
        return f"{e.name.upper()}({', '.join(to_text(a) for a in e.args)})"
    if isinstance(e, Agg):
        inner = "*" if e.arg is None else to_text(e.arg)
        text = f"{e.fn}({'DISTINCT ' if e.distinct else ''}{inner})"
        if e.condition is not None:
            text += f" FILTER (WHERE {to_text(e.condition)})"
        if e.level is not None and e.level.kind != "report":
            text += f" @{e.level.kind}{{{', '.join(e.level.dims)}}}"
            if e.level.filters == "ignore":
                text += " ignoring filters"
        return text
    if isinstance(e, BinOp):
        p = _PREC.get(e.op, 3)
        text = f"{to_text(e.left, p)} {e.op.upper() if e.op in ('and', 'or') else e.op} {to_text(e.right, p + 1)}"
        return f"({text})" if p < parent else text
    if isinstance(e, UnaryOp):
        if e.op == "is_null":
            return f"{to_text(e.arg, 6)} IS NULL"
        if e.op == "is_not_null":
            return f"{to_text(e.arg, 6)} IS NOT NULL"
        return f"{e.op.upper() if e.op == 'not' else e.op}{' ' if e.op == 'not' else ''}{to_text(e.arg, 6)}"
    if isinstance(e, Case):
        whens = " ".join(f"WHEN {to_text(c)} THEN {to_text(v)}" for c, v in e.whens)
        tail = f" ELSE {to_text(e.else_)}" if e.else_ is not None else ""
        return f"CASE {whens}{tail} END"
    if isinstance(e, Opaque):
        return f"<opaque {e.dialect}: {e.text}>"
    raise TypeError(type(e))
