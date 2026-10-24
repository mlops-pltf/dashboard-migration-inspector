"""SQL with explicit semantics, for ThoughtSpot SQL artifacts and ground-truth witness queries.

* Progressive joins: a query joins only the tables it needs, along the declared join paths.
* Chasm-safe: measures from different fact tables are computed separately and merged on the
  dimensions (as ThoughtSpot does).
* Fixed-level measures (group_aggregate) are computed at their level and joined back.

It also builds Freeform SQL the way a developer would write it for a Strategy table.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from . import DB, SCHEMA
from .domain import ALIAS_TO_TABLE, TABLE_INFO, Filter, SemanticModel
from .warehouse import TABLES

_PLACEHOLDER = re.compile(r"\{([a-z]{1,2})\.([a-z_]+)\}")
_NUMERIC = ("integer", "bigint", "decimal")


def fq(physical: str) -> str:
    return f"{DB}.{SCHEMA}.{physical}"


def _is_numeric(alias: str, column: str) -> bool:
    physical = TABLE_INFO[ALIAS_TO_TABLE[alias]][0]
    return TABLES[physical].col(column).sql_type.startswith(_NUMERIC)


@dataclass(frozen=True)
class SqlJoin:
    left: str  # alias on the many side
    right: str  # alias on the one side
    type: str  # LEFT | INNER
    keys: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Measure:
    expr: str
    home: str
    level: tuple[str, ...] = ()


@dataclass(frozen=True)
class Attribute:
    expr: str
    alias: str
    numeric: bool


@dataclass
class Semantics:
    relations: dict[str, str]  # alias -> FROM source
    joins: tuple[SqlJoin, ...]
    attrs: dict[str, Attribute]
    measures: dict[str, Measure]
    filters: tuple[Filter, ...]  # model-level, always applied
    progressive: bool = True


def _resolve(text: str, colmap: dict[str, str] | None = None, target_alias: str | None = None) -> str:
    def sub(m: re.Match) -> str:
        key = f"{m.group(1)}.{m.group(2)}"
        if colmap is None:
            return key
        return f"{target_alias}.{colmap[key]}"

    return _PLACEHOLDER.sub(sub, text)


def from_model(model: SemanticModel) -> Semantics:
    """Semantics of a model over the physical tables (ThoughtSpot, or a lookup-style Strategy model)."""
    relations = {TABLE_INFO[t][1]: fq(TABLE_INFO[t][0]) for t in model.tables}
    joins = tuple(
        SqlJoin(TABLE_INFO[j.source][1], TABLE_INFO[j.destination][1],
                "INNER" if j.type == "INNER" else "LEFT", j.keys)
        for j in model.joins
    )
    attrs, measures = {}, {}
    for f in model.fields:
        if f.kind == "ATTRIBUTE":
            alias, column = _PLACEHOLDER.search(f.sql).groups()
            attrs[f.name] = Attribute(_resolve(f.sql), alias, _is_numeric(alias, column))
        else:
            measures[f.name] = Measure(_resolve(f.sql), f.home, f.level)
    return Semantics(relations, joins, attrs, measures, model.filters, model.progressive)


def from_ffsql(model: SemanticModel, sql: str, colmap: dict[str, str], alias: str = "ff") -> Semantics:
    """Semantics of a single Freeform SQL table: every field reads a column of the SQL's output."""
    attrs, measures = {}, {}
    for f in model.fields:
        if f.kind == "ATTRIBUTE":
            a, column = _PLACEHOLDER.search(f.sql).groups()
            attrs[f.name] = Attribute(_resolve(f.sql, colmap, alias), alias, _is_numeric(a, column))
        else:
            measures[f.name] = Measure(_resolve(f.sql, colmap, alias), alias, f.level)
    # the model filter, if any, lives inside the SQL text; the Strategy model itself has none
    return Semantics({alias: f"(\n{_indent(sql, 4)}\n)"}, (), attrs, measures, (), True)


def _indent(text: str, n: int) -> str:
    return "\n".join(" " * n + line if line else line for line in text.splitlines())


def _path(sem: Semantics, home: str, target: str) -> list[SqlJoin]:
    """Directed BFS from the fact (many side) to a dimension (one side)."""
    frontier, seen = [(home, [])], {home}
    while frontier:
        nxt = []
        for alias, path in frontier:
            if alias == target:
                return path
            for j in sem.joins:
                if j.left == alias and j.right not in seen:
                    seen.add(j.right)
                    nxt.append((j.right, path + [j]))
        frontier = nxt
    raise ValueError(f"no join path from {home} to {target}")


def _literal(attr: Attribute, value: str) -> str:
    return value if attr.numeric else "'" + value.replace("'", "''") + "'"


def _predicate(sem: Semantics, flt: Filter) -> str:
    a = sem.attrs[flt.field]
    values = ", ".join(_literal(a, v) for v in flt.values)
    return f"{a.expr} {flt.oper.upper()} ({values})"


def _block(sem: Semantics, home: str, dims: tuple[str, ...], measures: list[str],
           filters: tuple[Filter, ...]) -> str:
    all_filters = tuple(sem.filters) + tuple(filters)
    needed = [sem.attrs[d].alias for d in dims] + [sem.attrs[f.field].alias for f in all_filters]
    if not sem.progressive:
        needed += list(sem.relations)
    edges: list[SqlJoin] = []
    for alias in needed:
        if alias == home:
            continue
        for j in _path(sem, home, alias):
            if j not in edges:
                edges.append(j)

    select = [f'{sem.attrs[d].expr} AS "{d}"' for d in dims] + [f'{sem.measures[m].expr} AS "{m}"' for m in measures]
    lines = ["SELECT", ",\n".join("    " + s for s in select), f"FROM {sem.relations[home]} {home}"]
    for j in edges:
        cond = " AND ".join(f"{j.right}.{rc} = {j.left}.{lc}" for lc, rc in j.keys)
        lines.append(f"{'LEFT JOIN' if j.type == 'LEFT' else 'JOIN'} {sem.relations[j.right]} {j.right} ON {cond}")
    if all_filters:
        lines.append("WHERE " + "\n  AND ".join(_predicate(sem, f) for f in all_filters))
    if dims:
        lines.append("GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims))))
    return "\n".join(lines)


def _order(dims, measures, sort, top_n, qualifier="") -> str:
    out = []
    if sort:
        out.append(f'ORDER BY {qualifier}"{sort[0]}" {sort[1]}')
    elif dims:
        out.append("ORDER BY " + ", ".join(f'{qualifier}"{d}"' for d in dims))
    if top_n:
        out.append(f"LIMIT {top_n}")
    return "\n".join(out)


def build(sem: Semantics, dims: tuple[str, ...], measures: tuple[str, ...], filters: tuple[Filter, ...] = (),
          sort: tuple[str, str] | None = None, top_n: int | None = None) -> str:
    groups: OrderedDict[str, list[str]] = OrderedDict()
    level: list[str] = []
    for m in measures:
        (level if sem.measures[m].level else groups.setdefault(sem.measures[m].home, [])).append(m)

    if len(groups) <= 1 and not level:
        home = next(iter(groups)) if groups else next(iter(sem.relations))
        body = _block(sem, home, dims, groups.get(home, []), filters)
        tail = _order(dims, measures, sort, top_n)
        return body + ("\n" + tail if tail else "")

    ctes, base_parts = [], []
    for i, (home, ms) in enumerate(groups.items(), 1):
        ctes.append((f"q{i}", _block(sem, home, dims, ms, filters)))
        base_parts.append((f"q{i}", ms))
    if len(base_parts) == 1:
        base_name, base_measures = base_parts[0]
        base_select = [f'{base_name}."{d}"' for d in dims] + [f'{base_name}."{m}"' for m in base_measures]
        base_from = base_name
    else:  # several facts: merge on the dimensions (UNION ALL + GROUP BY keeps NULL keys together)
        union = []
        for name, ms in base_parts:
            cols = [f'"{d}"' for d in dims] + [f'"{m}"' if m in ms else f'NULL AS "{m}"'
                                                for _, other in base_parts for m in other]
            union.append(f"SELECT {', '.join(cols)} FROM {name}")
        all_m = [m for _, ms in base_parts for m in ms]
        merged_cols = [f'"{d}"' for d in dims] + [f'MAX("{m}") AS "{m}"' for m in all_m]
        union_sql = "\nUNION ALL\n".join(union)
        merged = f"SELECT {', '.join(merged_cols)}\nFROM (\n{_indent(union_sql, 4)}\n) u"
        if dims:
            merged += "\nGROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        ctes.append(("merged", merged))
        base_select = [f'merged."{d}"' for d in dims] + [f'merged."{m}"' for m in all_m]
        base_from = "merged"

    joins = []
    for k, m in enumerate(level, 1):
        lv = sem.measures[m].level
        missing = [d for d in lv if d not in dims]
        if missing:
            raise ValueError(f"fixed-level measure {m!r} needs {missing} in the query")
        name = f"lvl{k}"
        ctes.append((name, _block(sem, sem.measures[m].home, lv, [m], filters)))
        cond = " AND ".join(f'{name}."{d}" IS NOT DISTINCT FROM {base_from}."{d}"' for d in lv)
        joins.append(f"LEFT JOIN {name} ON {cond}")
        base_select.append(f'{name}."{m}"')

    with_sql = "WITH " + ",\n".join(f"{n} AS (\n{_indent(b, 4)}\n)" for n, b in ctes)
    if not groups:  # only level measures: anchor on the level CTE itself
        raise ValueError("queries with only fixed-level measures are not supported")
    body = "SELECT\n" + ",\n".join("    " + s for s in base_select) + f"\nFROM {base_from}"
    if joins:
        body += "\n" + "\n".join(joins)
    tail = _order(dims, measures, sort, top_n)
    return f"{with_sql}\n{body}" + ("\n" + tail if tail else "")


# --------------------------------------------------------------------------------------------
# Freeform SQL, as a Strategy developer would write it for one model
# --------------------------------------------------------------------------------------------
_PREFIX = {"DIM_CUSTOMER": "customer", "DIM_STORE": "store", "DIM_PRODUCT": "product", "DIM_DATE": "date"}


@dataclass(frozen=True)
class FreeformSql:
    sql: str
    colmap: dict[str, str]  # "{alias}.{column}" -> output column
    columns: tuple[tuple[str, str], ...]  # (output column, source "alias.column")


def freeform_sql(model: SemanticModel, *, join_types: dict[str, str] | None = None, where: bool = True,
                 extra_joins: tuple[str, ...] = (), extra_columns: tuple[tuple[str, str], ...] = ()) -> FreeformSql:
    """One SELECT that pre-joins every table of ``model``.

    ``join_types`` overrides the join keyword per destination table (e.g. {"DIM_CUSTOMER": "INNER"}).
    ``where=False`` drops the model filter. ``extra_joins``/``extra_columns`` add raw clauses.
    """
    join_types = join_types or {}
    refs: list[tuple[str, str]] = []
    for f in model.fields:
        for alias, column in _PLACEHOLDER.findall(f.sql):
            if (alias, column) not in refs:
                refs.append((alias, column))

    names: dict[str, list[str]] = {}
    for alias, column in refs:
        names.setdefault(column, []).append(alias)
    colmap, columns = {}, []
    for alias, column in refs:
        table = ALIAS_TO_TABLE[alias]
        out = column
        if len(names[column]) > 1 and table in _PREFIX and not column.startswith(_PREFIX[table]):
            out = f"{_PREFIX[table]}_{column}"
        colmap[f"{alias}.{column}"] = out
        columns.append((out, f"{alias}.{column}"))
    columns.extend((out, src) for src, out in extra_columns)

    root = model.tables[0]
    order, edges, seen = [root], [], {root}
    while True:  # breadth-first over the (undirected) join graph from the first fact table
        added = False
        for j in model.joins:
            for here, there in ((j.source, j.destination), (j.destination, j.source)):
                if here in seen and there not in seen:
                    seen.add(there)
                    order.append(there)
                    edges.append((there, j))
                    added = True
        if not added:
            break

    select = ",\n".join(f"    {src}" + ("" if src.split(".")[1] == out else f" AS {out}") for out, src in columns)
    lines = [f"SELECT\n{select}", f"FROM {fq(TABLE_INFO[root][0])} {TABLE_INFO[root][1]}"]
    for table, j in edges:
        kind = join_types.get(table, "INNER" if j.type == "INNER" else "LEFT")
        alias = TABLE_INFO[table][1]
        cond = " AND ".join(f"{TABLE_INFO[j.destination][1]}.{rc} = {TABLE_INFO[j.source][1]}.{lc}"
                            for lc, rc in j.keys)
        lines.append(f"{'JOIN' if kind == 'INNER' else 'LEFT JOIN'} {fq(TABLE_INFO[table][0])} {alias} ON {cond}")
    lines.extend(extra_joins)
    if where and model.filters:
        preds = []
        for flt in model.filters:
            f = model.field(flt.field)
            expr = _resolve(f.sql)
            if flt.oper == "not in" and len(flt.values) == 1:
                preds.append(f"{expr} <> '{flt.values[0]}'")
            else:
                vals = ", ".join(f"'{v}'" for v in flt.values)
                preds.append(f"{expr} {flt.oper.upper()} ({vals})")
        lines.append("WHERE " + "\n  AND ".join(preds))
    return FreeformSql("\n".join(lines), colmap, tuple(columns))
