"""Reconcile two result sets inside the warehouse.

Both queries return the same key columns (the dimensions) and one or more measures. The sets are full-outer
joined on the keys (NULL-safe) and each measure is compared within tolerance. Only counts, totals and a few
mismatching rows come back, so the size of the result sets does not matter.
"""

from __future__ import annotations

from ..sql_guard.guard import guard
from ..warehouse.connection import fetch, identifier

SAMPLES = 20
_NULL_KEY = "'~null~'"
_GRAND = "grand__"


def run(sql: str) -> tuple[list[str], list[list]]:
    g = guard(sql)
    if not g.ok:
        raise ValueError("query rejected: " + "; ".join(g.reasons))
    return fetch(g.sql)


def _pairs(measures: list) -> list[tuple[str, str]]:
    out = []
    for m in measures:
        if isinstance(m, str):
            out.append((identifier(m), identifier(m)))
        elif isinstance(m, (list, tuple)) and len(m) == 2:
            out.append((identifier(m[0]), identifier(m[1])))
        elif isinstance(m, dict) and {"left", "right"} <= set(m):
            out.append((identifier(m["left"]), identifier(m["right"])))
        else:
            raise ValueError(f"measure must be a name, [left, right] or {{left, right}}: {m!r}")
    if not out:
        raise ValueError("at least one measure required")
    return out


def compare_queries(left_sql: str, right_sql: str, keys: list[str] | None, measures: list,
                    abs_tolerance: float = 0.01, rel_tolerance: float = 0.0) -> dict:
    pairs = _pairs(measures)
    keys = [identifier(k) for k in (keys or [])]
    lsql, rsql = left_sql.strip().rstrip(";"), right_sql.strip().rstrip(";")
    if not keys:
        keys = [_GRAND]
        lsql = f"SELECT 'all' AS {_GRAND}, q.* FROM ({lsql}) AS q"
        rsql = f"SELECT 'all' AS {_GRAND}, q.* FROM ({rsql}) AS q"

    def key_text(alias, k):
        return f"COALESCE(CAST({alias}.{k} AS VARCHAR), {_NULL_KEY})"

    on = " AND ".join(f"{key_text('l', k)} = {key_text('r', k)}" for k in keys)
    key_cols = ", ".join(f"COALESCE(CAST(l.{k} AS VARCHAR), CAST(r.{k} AS VARCHAR)) AS {k}" for k in keys)
    measure_cols = ", ".join(f"CAST(l.{a} AS DOUBLE PRECISION) AS l_{i}, CAST(r.{b} AS DOUBLE PRECISION) AS r_{i}"
                             for i, (a, b) in enumerate(pairs))
    presence = f"CASE WHEN l.{keys[0]}__p IS NULL THEN 'right_only' WHEN r.{keys[0]}__p IS NULL THEN 'left_only' " \
               f"ELSE 'both' END AS presence"
    tol = f"{float(abs_tolerance)!r}", f"{float(rel_tolerance)!r}"

    def same(i):
        lv, rv = f"l_{i}", f"r_{i}"
        return (f"CASE WHEN {lv} IS NULL AND {rv} IS NULL THEN 1 WHEN {lv} IS NULL OR {rv} IS NULL THEN 0 "
                f"WHEN ABS({lv} - {rv}) <= GREATEST({tol[0]}, {tol[1]} * GREATEST(ABS({lv}), ABS({rv}))) THEN 1 "
                f"ELSE 0 END")

    all_same = " * ".join(same(i) for i in range(len(pairs)))
    base = (f"WITH ls AS (SELECT 1 AS {keys[0]}__p, q.* FROM ({lsql}) AS q), "
            f"rs AS (SELECT 1 AS {keys[0]}__p, q.* FROM ({rsql}) AS q), "
            f"j AS (SELECT {key_cols}, {measure_cols}, {presence} "
            f"FROM ls AS l FULL OUTER JOIN rs AS r ON {on}), "
            f"c AS (SELECT j.*, CASE WHEN presence = 'both' THEN {all_same} ELSE 0 END AS ok FROM j)")

    sums = ", ".join(f"SUM(l_{i}), SUM(r_{i})" for i in range(len(pairs)))
    per_measure = ", ".join(f"SUM(CASE WHEN presence = 'both' AND {same(i)} = 0 THEN 1 ELSE 0 END)"
                            for i in range(len(pairs)))
    _, summary = run(f"{base} SELECT COUNT(*), "
                     f"SUM(CASE WHEN presence = 'both' THEN 1 ELSE 0 END), "
                     f"SUM(CASE WHEN presence = 'left_only' THEN 1 ELSE 0 END), "
                     f"SUM(CASE WHEN presence = 'right_only' THEN 1 ELSE 0 END), "
                     f"SUM(ok), {sums}, {per_measure} FROM c")
    row = summary[0]
    total, both, left_only, right_only, matched = (v or 0 for v in row[:5])
    n = len(pairs)
    sum_vals, mism = row[5:5 + 2 * n], row[5 + 2 * n:]

    sample_cols = ", ".join(keys + [f"l_{i}, r_{i}" for i in range(n)] + ["presence"])
    _, sample = run(f"{base} SELECT {sample_cols} FROM c WHERE ok = 0 "
                    f"ORDER BY {', '.join(f'ABS(COALESCE(l_{i}, 0) - COALESCE(r_{i}, 0))' for i in range(n))} DESC "
                    f"LIMIT {SAMPLES}")
    shown_keys = [] if keys == [_GRAND] else keys

    def sample_row(r):
        out = {k: r[j] for j, k in enumerate(keys) if k in shown_keys}
        for i, (a, b) in enumerate(pairs):
            out[f"left.{a}"], out[f"right.{b}"] = r[len(keys) + 2 * i], r[len(keys) + 2 * i + 1]
        out["presence"] = r[-1]
        return out

    measures_out = []
    for i, (a, b) in enumerate(pairs):
        lt, rt = sum_vals[2 * i], sum_vals[2 * i + 1]
        measures_out.append({"left": a, "right": b, "left_total": lt, "right_total": rt,
                             "total_difference": None if lt is None or rt is None else round(rt - lt, 6),
                             "mismatched_rows": mism[i] or 0})
    status = "MATCH" if matched == total and total > 0 else ("EMPTY" if total == 0 else "MISMATCH")
    return {"status": status, "keys": shown_keys, "rows": total, "rows_in_both": both,
            "left_only_rows": left_only, "right_only_rows": right_only, "matching_rows": matched,
            "measures": measures_out, "tolerance": {"abs": abs_tolerance, "rel": rel_tolerance},
            "mismatch_samples": [sample_row(r) for r in sample]}
