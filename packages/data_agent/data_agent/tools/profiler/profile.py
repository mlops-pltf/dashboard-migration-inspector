"""Profiles that settle join questions: is a key unique, and how many rows find no partner across a join.

A relation is either a table (``schema.table``) or a SELECT. All SQL built here goes through the guard like any
other query.
"""

from __future__ import annotations

from ..sql_guard.guard import guard
from ..warehouse.connection import fetch, identifier

SAMPLES = 10


def relation(source: str) -> str:
    text = source.strip().rstrip(";")
    if " " not in text and "(" not in text:
        return identifier(text)
    return f"({text})"


def run(sql: str) -> tuple[list[str], list[list]]:
    g = guard(sql)
    if not g.ok:
        raise ValueError("query rejected: " + "; ".join(g.reasons))
    return fetch(g.sql)


def _cols(keys: list[str], alias: str) -> list[str]:
    return [f"{alias}.{identifier(k)}" for k in keys]


def key_profile(source: str, keys: list[str]) -> dict:
    if not keys:
        raise ValueError("keys required")
    rel, k = relation(source), [identifier(c) for c in keys]
    key_list = ", ".join(k)
    any_null = " OR ".join(f"{c} IS NULL" for c in k)
    _, rows = run(f"SELECT COUNT(*), SUM(CASE WHEN {any_null} THEN 1 ELSE 0 END) FROM {rel} AS t")
    total, nulls = rows[0][0], rows[0][1] or 0
    _, dup = run(f"SELECT COUNT(*), COALESCE(SUM(n), 0), COALESCE(MAX(n), 0) FROM "
                 f"(SELECT {key_list}, COUNT(*) AS n FROM {rel} AS t GROUP BY {key_list} HAVING COUNT(*) > 1) AS d")
    dup_keys, dup_rows, max_n = (int(v) for v in dup[0])
    _, distinct = run(f"SELECT COUNT(*) FROM (SELECT {key_list} FROM {rel} AS t GROUP BY {key_list}) AS g")
    _, sample = run(f"SELECT {key_list}, COUNT(*) AS n FROM {rel} AS t GROUP BY {key_list} "
                    f"HAVING COUNT(*) > 1 ORDER BY n DESC LIMIT {SAMPLES}")
    return {"source": source, "keys": k, "rows": total, "distinct_keys": distinct[0][0], "null_key_rows": nulls,
            "duplicate_keys": dup_keys, "rows_in_duplicate_keys": dup_rows, "max_rows_per_key": max_n,
            "unique": dup_keys == 0, "duplicate_samples": [dict(zip(k + ["n"], r)) for r in sample]}


def orphan_profile(left: str, right: str, left_keys: list[str], right_keys: list[str] | None = None) -> dict:
    right_keys = right_keys or left_keys
    if not left_keys or len(left_keys) != len(right_keys):
        raise ValueError("left_keys and right_keys must be non-empty and the same length")
    lrel, rrel = relation(left), relation(right)
    on = " AND ".join(f"{a} = {b}" for a, b in zip(_cols(left_keys, "l"), _cols(right_keys, "r")))
    lk, rk = _cols(left_keys, "l"), _cols(right_keys, "r")
    l_null = " OR ".join(f"{c} IS NULL" for c in lk)
    r_null = " OR ".join(f"{c} IS NULL" for c in rk)

    def side(rel_a, alias_a, rel_b, alias_b, keys_a, null_a):
        _, rows = run(f"SELECT COUNT(*), "
                      f"SUM(CASE WHEN {null_a} THEN 1 ELSE 0 END), "
                      f"SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM {rel_b} AS {alias_b} WHERE {on}) THEN 1 ELSE 0 END) "
                      f"FROM {rel_a} AS {alias_a}")
        total, nulls, orphans = rows[0][0], rows[0][1] or 0, rows[0][2] or 0
        _, sample = run(f"SELECT DISTINCT {', '.join(keys_a)} FROM {rel_a} AS {alias_a} "
                        f"WHERE NOT EXISTS (SELECT 1 FROM {rel_b} AS {alias_b} WHERE {on}) LIMIT {SAMPLES}")
        return {"rows": total, "null_key_rows": nulls, "orphan_rows": orphans,
                "orphan_samples": [list(r) for r in sample]}

    return {"left": left, "right": right, "left_keys": left_keys, "right_keys": right_keys,
            "left_side": side(lrel, "l", rrel, "r", lk, l_null),
            "right_side": side(rrel, "r", lrel, "l", rk, r_null),
            "note": "left_side.orphan_rows = left rows with no right partner (lost by INNER, kept by LEFT); "
                    "right_side.orphan_rows = right rows with no left partner (lost unless RIGHT/FULL)."}


def column_profile(source: str, column: str, top: int = 10) -> dict:
    rel, c = relation(source), identifier(column)
    _, rows = run(f"SELECT COUNT(*), COUNT({c}), COUNT(DISTINCT {c}), MIN({c}), MAX({c}) FROM {rel} AS t")
    total, non_null, distinct, lo, hi = rows[0]
    _, values = run(f"SELECT {c}, COUNT(*) AS n FROM {rel} AS t GROUP BY {c} ORDER BY n DESC "
                    f"LIMIT {max(1, min(int(top), 50))}")
    return {"source": source, "column": c, "rows": total, "nulls": total - non_null, "distinct": distinct,
            "min": lo, "max": hi, "top_values": [{"value": v, "n": n} for v, n in values]}
