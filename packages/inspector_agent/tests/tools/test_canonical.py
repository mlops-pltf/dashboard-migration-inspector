"""Canonical forms, on expressions as the experts emit them."""

from inspector_contract import Col, to_text
from inspector_agent.tools.definition_comparer.canonical import canonical, same
from strategy_agent.tools.sql_analyzer.convert import parse_expression, to_ir
from strategy_agent.tools.strategy_reader import expression as mstr_expr
from thoughtspot_agent.tools.thoughtspot_reader import formula as ts_formula


def sql(text):
    return to_ir(parse_expression(text), lambda t, c: Col(table=t or "t", column=c))


def ts(text):
    return ts_formula.parse(text, lambda t, c: Col(table="t", column=c))


def ms(text):
    return mstr_expr.parse(text, lambda n: Col(table="t", column=n))


def test_safe_divide_equals_strategy_if_but_not_plain_division():
    assert same(ts("safe_divide ( sum ( [a] ) , sum ( [b] ) )"), ms("If((Sum(b) = 0), 0, (Sum(a) / Sum(b)))"))
    assert not same(ts("safe_divide ( sum ( [a] ) , sum ( [b] ) )"), ms("Sum(a) / Sum(b)"))


def test_nullif_divisor_and_not_in():
    assert same(sql("SUM(t.a) / NULLIF(COUNT(DISTINCT t.b), 0)"), ms("Sum(a) / Count<Distinct=True>(b)"))
    assert same(sql("NOT (t.s IN ('x'))"), sql("t.s <> 'x'"))


def test_commutative_flip_and_constants():
    assert same(sql("t.a + t.b * t.c"), sql("t.c * t.b + t.a"))
    assert same(sql("t.a > 3 AND t.b = 1"), sql("1 = t.b AND 3 < t.a"))
    assert not same(sql("t.a - t.b"), sql("t.b - t.a"))
    assert same(sql("t.a * (2 + 1)"), sql("t.a * 3.0"))


def test_levels_and_readability():
    assert same(ms("Sum(x){~+}"), ms("Sum(x)"))
    assert not same(ms("Sum(x){[Region]}"), ms("Sum(x)"))
    assert to_text(canonical(sql("'x' = t.s"))) == "[t::s] = 'x'"
