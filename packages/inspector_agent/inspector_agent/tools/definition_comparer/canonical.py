"""Canonical form of resolved expressions: equal canonical trees are a *proof* of equal meaning.

Every rewrite here preserves meaning exactly. IR semantics: ``a / b`` yields NULL when b = 0 (tool-specific
behaviour such as "0 when b = 0" is spelled out by the experts as an explicit CASE).
Anything that would need algebra beyond these rewrites (e.g. SUM(a - b) = SUM(a) - SUM(b)) is left
to the LLM judge and the data.
"""

from __future__ import annotations

from inspector_contract import Agg, BinOp, Expr, Func, Lit, UnaryOp, to_text, transform

_COMMUTATIVE = {"+", "*", "and", "or", "=", "!="}
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}


def _key(e: Expr) -> str:
    return to_text(e)


def _is_zero(e: Expr) -> bool:
    return isinstance(e, Lit) and e.type == "number" and e.value == 0


def _flatten(op: str, e: Expr) -> list[Expr]:
    if isinstance(e, BinOp) and e.op == op:
        return _flatten(op, e.left) + _flatten(op, e.right)
    return [e]


def _rebuild(op: str, parts: list[Expr]) -> Expr:
    out = parts[0]
    for p in parts[1:]:
        out = BinOp(op=op, left=out, right=p)
    return out


def _fold(op: str, a: Lit, b: Lit) -> Lit | None:
    if a.type != "number" or b.type != "number":
        return None
    try:
        value = {"+": a.value + b.value, "-": a.value - b.value, "*": a.value * b.value,
                 "/": a.value / b.value if b.value else None}[op]
    except KeyError:
        return None
    return None if value is None else Lit(value=float(value), type="number")


def _rule(e: Expr) -> Expr | None:
    if isinstance(e, Lit) and e.type == "number" and not isinstance(e.value, bool):
        return Lit(value=float(e.value), type="number") if not isinstance(e.value, float) else None

    if isinstance(e, Func):
        if e.name == "coalesce" and len(e.args) == 1:
            return e.args[0]
        if e.name == "in" and len(e.args) == 2:
            return canonical(BinOp(op="=", left=e.args[0], right=e.args[1]))
        if e.name == "in" and len(e.args) > 2:
            return e.model_copy(update={"args": (e.args[0], *sorted(e.args[1:], key=_key))})
        return None

    if isinstance(e, UnaryOp) and e.op == "not":
        a = e.arg
        if isinstance(a, UnaryOp) and a.op == "not":
            return a.arg
        if isinstance(a, BinOp) and a.op in ("=", "!="):
            return canonical(a.model_copy(update={"op": "!=" if a.op == "=" else "="}))
        if isinstance(a, UnaryOp) and a.op in ("is_null", "is_not_null"):
            return a.model_copy(update={"op": "is_not_null" if a.op == "is_null" else "is_null"})
        if isinstance(a, Func) and a.name == "in":
            return Func(name="not_in", args=a.args)
        return None

    if isinstance(e, BinOp):
        if e.op == "/" and isinstance(e.right, Func) and e.right.name == "nullif" and len(e.right.args) == 2 \
                and _is_zero(e.right.args[1]):
            return BinOp(op="/", left=e.left, right=e.right.args[0])  # division already yields NULL on zero
        if isinstance(e.left, Lit) and isinstance(e.right, Lit):
            folded = _fold(e.op, e.left, e.right)
            if folded is not None:
                return folded
        if e.op in _FLIP:
            if e.op in (">", ">="):
                return BinOp(op=_FLIP[e.op], left=e.right, right=e.left)
            return None
        if e.op in _COMMUTATIVE:
            # literals last so comparisons read naturally (column = 'value'); otherwise textual order
            parts = sorted(_flatten(e.op, e), key=lambda x: (isinstance(x, Lit), _key(x)))
            rebuilt = _rebuild(e.op, parts)
            return None if rebuilt == e else rebuilt
        return None

    if isinstance(e, Agg):
        level = e.level
        if level is not None and level.kind == "report" and level.filters == "apply":
            level = None
        elif level is not None:
            level = level.model_copy(update={"dims": tuple(sorted(level.dims))})
        if level != e.level:
            return e.model_copy(update={"level": level})
        return None
    return None


def canonical(e: Expr) -> Expr:
    """Apply the rewrites bottom-up until the tree stops changing."""
    for _ in range(10):
        out = transform(e, _rule)
        if out == e:
            return out
        e = out
    return e


def same(a: Expr, b: Expr) -> bool:
    return canonical(a) == canonical(b)
