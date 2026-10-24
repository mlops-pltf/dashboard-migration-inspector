"""ThoughtSpot search query (answer ``search_query``) -> QuerySpec.

Handles column tokens with date buckets (``[Order Date].monthly``), aggregation keywords
(``average [Revenue]``), ``top N`` / ``bottom N``, ``sort by [X] descending`` and simple filters
(``[Region] = 'West'``). Everything else (e.g. ``last 30 days``) is kept in ``unparsed``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from inspector_contract import FieldUse, FilterSpec, QuerySpec

_TOKEN = re.compile(r"""
    \s*(?:
      (?P<FIELD>\[[^\]]+\](?:\.[A-Za-z_]+)?)
    | (?P<STR>'(?:[^']|'')*'|"[^"]*")
    | (?P<OP>!=|>=|<=|=|<|>)
    | (?P<NUM>-?\d+(?:\.\d+)?)
    | (?P<WORD>[A-Za-z_][A-Za-z0-9_]*)
    )""", re.VERBOSE)

_AGG_WORDS = {"sum": "SUM", "average": "AVG", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX",
              "unique": "COUNT_DISTINCT"}

# kind_of(field name) -> "ATTRIBUTE" | "MEASURE" | None (unknown)
KindLookup = Callable[[str], str | None]


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            out.append(("OTHER", text[pos]))
            pos += 1
            continue
        out.append((m.lastgroup, m.group(m.lastgroup)))
        pos = m.end()
    return out


def _field(tok: str) -> tuple[str, str | None]:
    name, _, bucket = tok.partition("].")
    return name.lstrip("[").rstrip("]").strip(), (bucket or None)


def parse(text: str, kind_of: KindLookup) -> QuerySpec:
    toks = _tokens(text)
    dims, measures, filters, sort, unparsed = [], [], [], [], []
    top_n = None
    i = 0
    while i < len(toks):
        kind, tok = toks[i]
        low = tok.lower()
        nxt = toks[i + 1] if i + 1 < len(toks) else (None, None)

        if kind == "WORD" and low in ("top", "bottom") and nxt[0] == "NUM":
            top_n = int(float(nxt[1]))
            if low == "bottom":
                unparsed.append(f"bottom {top_n}")
            i += 2
            continue
        if kind == "WORD" and low == "sort" and nxt[1] and nxt[1].lower() == "by" and i + 2 < len(toks):
            name, _ = _field(toks[i + 2][1])
            direction = "ASC"
            j = i + 3
            if j < len(toks) and toks[j][1].lower() in ("descending", "desc", "ascending", "asc"):
                direction = "DESC" if toks[j][1].lower().startswith("desc") else "ASC"
                j += 1
            sort.append((name, direction))
            i = j
            continue
        if kind == "WORD" and low in _AGG_WORDS:
            agg = _AGG_WORDS[low]
            j = i + 1
            if low == "unique" and j < len(toks) and toks[j][1].lower() == "count":
                j += 1
            if j < len(toks) and toks[j][0] == "FIELD":
                name, bucket = _field(toks[j][1])
                measures.append(FieldUse(field=name, bucket=bucket, aggregation=agg))
                i = j + 1
                continue
        if kind == "FIELD":
            name, bucket = _field(tok)
            if nxt[0] == "OP" and i + 2 < len(toks) and toks[i + 2][0] in ("STR", "NUM", "WORD"):
                value = toks[i + 2][1]
                if toks[i + 2][0] == "STR":
                    value = value[1:-1]
                filters.append(FilterSpec(field=name, op=nxt[1], values=(value,)))
                i += 3
                continue
            use = FieldUse(field=name, bucket=bucket)
            (measures if kind_of(name) == "MEASURE" else dims).append(use)
            i += 1
            continue
        unparsed.append(tok)
        i += 1

    return QuerySpec(dimensions=tuple(dims), measures=tuple(measures), filters=tuple(filters),
                     sort=tuple(sort), top_n=top_n, unparsed=tuple(_join_words(unparsed)))


def _join_words(words: list[str]) -> list[str]:
    """Collapse runs of loose words into phrases ('last', '30', 'days' -> 'last 30 days')."""
    return [" ".join(words)] if words else []
