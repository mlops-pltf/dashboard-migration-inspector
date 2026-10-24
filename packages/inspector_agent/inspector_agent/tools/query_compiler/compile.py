"""Compile a query on a semantic model (measures by dimensions, with filters) into one SQL statement.

The SQL is what the model *as exported* computes, so both sides can be run against the warehouse and compared.
It follows the semantics the experts made explicit in the IR, the same for every BI tool:

* each aggregate is computed from its home table, joining only the tables the query needs (plus joins applied
  to every query), with each join's type seen from the home table;
* aggregates with different homes are computed separately and merged on the dimensions (no fan/chasm traps);
* aggregates at a fixed / exclude level are computed at that grain and joined back on its dimensions;
* model filters always apply; query filters too, unless an aggregate ignores them;
* ``a / b`` returns NULL when b = 0 (BI division semantics), ``safe_divide`` arrives as an explicit CASE.

Output columns: d0, d1 ... (dimensions, in the order asked) and m0, m1 ... (measures). Whatever cannot be
compiled faithfully is reported in ``unsupported`` (no SQL), and every guess in ``assumptions``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from inspector_contract import (
    Agg, BinOp, Case, Col, Expr, FieldUse, FilterSpec, Func, Join, Lit, Opaque, PCol, Ref, SemanticModel, UnaryOp,
    transform, walk,
)
from ..lineage_tracer.resolve import resolve_expr

BUCKETS = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}
_OPS = {"=", "!=", "<", "<=", ">", ">=", "+", "-", "*", "%", "||", "and", "or"}


class Unsupported(Exception):
    pass


@dataclass
class Compiled:
    sql: str | None
    dimensions: list[str]
    measures: list[str]
    assumptions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"sql": self.sql, "dimensions": {f"d{i}": d for i, d in enumerate(self.dimensions)},
                "measures": {f"m{i}": m for i, m in enumerate(self.measures)},
                "assumptions": self.assumptions, "unsupported": self.unsupported}


@dataclass
class _Leaf:
    agg: Agg
    home: str
    level_kind: str  # report | fixed
    level_dims: tuple[int, ...]  # indexes into the query dimensions
    ignore_filters: bool
    alias: str = ""


def compile_query(model: SemanticModel, measures: list[str], dimensions: list[str | FieldUse] = (),
                  filters: list[FilterSpec] = (), *, unknown_join_as: str = "LEFT") -> Compiled:
    dims = [d if isinstance(d, FieldUse) else FieldUse(field=d) for d in dimensions]
    c = _Compiler(model, unknown_join_as)
    out = Compiled(None, [d.field for d in dims], list(measures))
    try:
        out.sql = c.compile(measures, dims, list(filters))
    except Unsupported as exc:
        out.unsupported.append(str(exc))
    out.assumptions = list(dict.fromkeys(c.assumptions))
    return out


class _Compiler:
    def __init__(self, model: SemanticModel, unknown_join_as: str):
        self.m = model
        self.unknown_join_as = unknown_join_as
        self.assumptions: list[str] = []
        self.adj: dict[str, list[tuple[Join, str]]] = {}
        for j in model.joins:
            self.adj.setdefault(j.left, []).append((j, j.right))
            self.adj.setdefault(j.right, []).append((j, j.left))

    # -- logical expansion ------------------------------------------------------------------------
    def expand(self, e: Expr, stack: tuple[str, ...] = ()) -> Expr:
        def fn(node: Expr) -> Expr | None:
            if isinstance(node, Ref):
                f = self.m.field(node.name)
                if f is None:
                    raise Unsupported(f"unknown reference [{node.name}]")
                if f.name in stack:
                    raise Unsupported(f"circular reference via [{node.name}]")
                return self.expand(f.expr, stack + (f.name,))
            if isinstance(node, Opaque):
                raise Unsupported(f"{node.text!r} has no usable definition ({node.reason or node.dialect})")
            return None

        return transform(e, fn)

    @staticmethod
    def tables_of(e: Expr) -> list[str]:
        return list(dict.fromkeys(n.table for n in walk(e) if isinstance(n, Col)))

    def candidates(self, name: str) -> list[Expr]:
        """Every expression an attribute can be read from (Strategy attributes live on several tables)."""
        table, sep, column = name.rpartition("::")
        if sep and self.m.table(table) is not None:
            return [Col(table=table, column=column)]
        f = self.m.field(name)
        if f is None:
            raise Unsupported(f"{name!r} is not a field of {self.m.name}")
        out = [self.expand(f.expr, (f.name,))]
        out += [self.expand(p.expr, (f.name,)) for p in f.placements]
        return list(dict.fromkeys(out))

    def physical(self, e: Expr) -> str | None:
        resolved, _ = resolve_expr(self.m, e)
        return str(resolved.column) if isinstance(resolved, PCol) else None

    # -- join paths -------------------------------------------------------------------------------
    def tree(self, home: str) -> dict[str, tuple[Join, str]]:
        """BFS tree from the home table: table -> (join used to reach it, parent table)."""
        parent: dict[str, tuple[Join, str]] = {}
        seen, queue = {home}, deque([home])
        while queue:
            t = queue.popleft()
            for j, other in self.adj.get(t, []):
                if other not in seen:
                    seen.add(other)
                    parent[other] = (j, t)
                    queue.append(other)
        return parent

    def choose(self, cands: list[Expr], home: str, tree: dict) -> Expr | None:
        def cost(e: Expr) -> int | None:
            total = 0
            for t in self.tables_of(e):
                if t == home:
                    continue
                if t not in tree:
                    return None
                depth, cur = 0, t
                while cur != home:
                    cur = tree[cur][1]
                    depth += 1
                total += depth
            return total

        scored = [(cost(e), i, e) for i, e in enumerate(cands)]
        scored = [s for s in scored if s[0] is not None]
        return min(scored)[2] if scored else None

    def joins_for(self, home: str, tables: set[str], tree: dict) -> list[tuple[Join, str, str]]:
        """Joins (join, from table, to table) connecting ``tables`` to home, plus joins applied to every query."""
        needed: set[str] = set()
        for t in tables:
            cur = t
            while cur != home:
                if cur not in tree:
                    raise Unsupported(f"no join path from {home} to {t}")
                needed.add(cur)
                cur = tree[cur][1]
        changed = True
        while changed:
            changed = False
            for t, (j, p) in tree.items():
                if j.applied == "always" and t not in needed and (p == home or p in needed):
                    needed.add(t)
                    changed = True
        order = []
        for t in tree:  # BFS order: parents first
            if t in needed:
                j, p = tree[t]
                order.append((j, p, t))
        return order

    def join_type(self, j: Join, from_table: str) -> str:
        t = j.type
        if t == "UNKNOWN":
            t = self.unknown_join_as
            self.assumptions.append(f"join {j.id}: type unknown in the export, compiled as {t}")
        if from_table == j.right:
            t = {"LEFT": "RIGHT", "RIGHT": "LEFT"}.get(t, t)
        return {"INNER": "INNER JOIN", "LEFT": "LEFT JOIN", "RIGHT": "RIGHT JOIN", "FULL": "FULL OUTER JOIN",
                "CROSS": "CROSS JOIN"}[t]

    # -- SQL rendering ----------------------------------------------------------------------------
    def relation(self, table_id: str) -> str:
        t = self.m.table(table_id)
        if t is None:
            raise Unsupported(f"unknown table {table_id!r}")
        rel = t.relation
        if rel.kind == "physical" and rel.table is not None:
            return f"{rel.table.namespace}.{rel.table.table}"
        if rel.kind == "sql" and rel.sql:
            return f"({rel.sql.strip().rstrip(';')})"
        raise Unsupported(f"table {t.name} has no usable relation")

    def column(self, col: Col, aliases: dict[str, str]) -> str:
        t = self.m.table(col.table)
        if t is None or col.table not in aliases:
            raise Unsupported(f"column {col.table}::{col.column} is not reachable")
        cdef = t.column(col.column)
        return f'{aliases[col.table]}."{(cdef.physical if cdef else col.column).lower()}"'

    def sql(self, e: Expr, aliases: dict[str, str], leaves: dict[int, str] | None = None) -> str:
        r = lambda x: self.sql(x, aliases, leaves)  # noqa: E731
        if isinstance(e, Col):
            return self.column(e, aliases)
        if isinstance(e, Lit):
            if e.value is None or e.type == "null":
                return "NULL"
            if e.type == "boolean" or isinstance(e.value, bool):
                return "TRUE" if e.value else "FALSE"
            if e.type == "number":
                return str(e.value)
            return "'" + str(e.value).replace("'", "''") + "'"
        if isinstance(e, Agg):
            if leaves is not None:
                return leaves[id(e)]
            arg = "*" if e.arg is None else r(e.arg)
            fn = e.fn
            distinct = e.distinct
            if fn == "COUNT_DISTINCT":
                fn, distinct = "COUNT", True
            if e.condition is not None:
                arg = f"CASE WHEN {r(e.condition)} THEN {'1' if e.arg is None else arg} END"
            return f"{fn}({'DISTINCT ' if distinct else ''}{arg})"
        if isinstance(e, BinOp):
            if e.op == "/":
                return f"(CAST({r(e.left)} AS DOUBLE PRECISION) / NULLIF({r(e.right)}, 0))"
            if e.op not in _OPS:
                raise Unsupported(f"operator {e.op!r}")
            op = {"!=": "<>"}.get(e.op, e.op.upper())
            return f"({r(e.left)} {op} {r(e.right)})"
        if isinstance(e, UnaryOp):
            if e.op == "is_null":
                return f"({r(e.arg)} IS NULL)"
            if e.op == "is_not_null":
                return f"({r(e.arg)} IS NOT NULL)"
            if e.op == "not":
                return f"(NOT {r(e.arg)})"
            if e.op == "-":
                return f"(-{r(e.arg)})"
            raise Unsupported(f"unary operator {e.op!r}")
        if isinstance(e, Case):
            whens = " ".join(f"WHEN {r(c)} THEN {r(v)}" for c, v in e.whens)
            tail = f" ELSE {r(e.else_)}" if e.else_ is not None else ""
            return f"CASE {whens}{tail} END"
        if isinstance(e, Func):
            args = list(e.args)
            if e.name in ("in", "not_in"):
                items = args[1].args if len(args) == 2 and isinstance(args[1], Func) and args[1].name == "set" \
                    else args[1:]
                neg = "NOT " if e.name == "not_in" else ""
                return f"({r(args[0])} {neg}IN ({', '.join(r(a) for a in items)}))"
            if e.name == "between":
                return f"({r(args[0])} BETWEEN {r(args[1])} AND {r(args[2])})"
            if e.name == "cast":
                return f"CAST({r(args[0])} AS {args[1].value})"
            if e.name == "safe_divide":
                a, b = r(args[0]), r(args[1])
                return f"CASE WHEN {b} = 0 THEN 0 ELSE CAST({a} AS DOUBLE PRECISION) / NULLIF({b}, 0) END"
            if e.name == "set":
                return ", ".join(r(a) for a in args)
            return f"{e.name.upper()}({', '.join(r(a) for a in args)})"
        raise Unsupported(f"cannot render {type(e).__name__}")

    # -- the query --------------------------------------------------------------------------------
    def dim_expr(self, use: FieldUse, home: str, tree: dict) -> Expr:
        chosen = self.choose(self.candidates(use.field), home, tree)
        if chosen is None:
            raise Unsupported(f"{use.field!r} cannot be reached from {self.m.table(home).name}")
        return chosen

    def dim_sql(self, use: FieldUse, e: Expr, aliases: dict[str, str]) -> str:
        text = self.sql(e, aliases)
        if use.bucket:
            unit = BUCKETS.get(use.bucket.lower())
            if unit is None:
                raise Unsupported(f"date bucket {use.bucket!r}")
            text = f"DATE_TRUNC('{unit}', {text})"
        return text

    def filter_expr(self, f: FilterSpec, home: str, tree: dict) -> Expr | None:
        cands = self.candidates(f.field)
        e = self.choose(cands, home, tree)
        if e is None:
            return None
        vals = tuple(Lit(value=v) for v in f.values)
        op = f.op.lower()
        if op in ("in", "not in"):
            return Func(name="in" if op == "in" else "not_in", args=(e, *vals))
        if op == "between" and len(vals) == 2:
            return Func(name="between", args=(e, *vals))
        if op in ("=", "!=", "<", "<=", ">", ">=") and len(vals) == 1:
            return BinOp(op=op, left=e, right=vals[0])
        raise Unsupported(f"filter operator {f.op!r} on {f.field}")

    def leaves(self, measures: list[str], dims: list[FieldUse]) -> tuple[list[Expr], list[_Leaf]]:
        exprs, leaves = [], []
        dim_phys = []
        for d in dims:
            cands = self.candidates(d.field)
            dim_phys.append({p for p in (self.physical(c) for c in cands) if p} | {d.field.lower()})
        for name in measures:
            f = self.m.field(name)
            if f is None:
                raise Unsupported(f"{name!r} is not a field of {self.m.name}")
            e = self.expand(f.expr, (f.name,))
            for node in walk(e):
                if isinstance(node, Agg):
                    leaves.append(self.leaf(node, dims, dim_phys))
            outside = _outside_aggregates(e)
            if outside:
                raise Unsupported(f"{name}: columns used outside an aggregate ({', '.join(outside)})")
            exprs.append(e)
        return exprs, leaves

    def leaf(self, a: Agg, dims: list[FieldUse], dim_phys: list[set[str]]) -> _Leaf:
        inner = [x for x in (a.arg, a.condition) if x is not None]
        tables = [t for x in inner for t in self.tables_of(x)]
        if any(isinstance(n, Agg) for x in inner for n in walk(x)):
            raise Unsupported("nested aggregates")
        if not tables:
            raise Unsupported("an aggregate reads no column (COUNT(*) without a table)")
        home = tables[0]
        level = a.level
        if level is None or level.kind == "report":
            return _Leaf(a, home, "report", tuple(range(len(dims))), False)
        idx = []
        for d in level.dims:
            names = {d.lower()}
            try:
                names |= {p for p in (self.physical(c) for c in self.candidates(d)) if p}
            except Unsupported:
                pass
            hit = next((i for i, ph in enumerate(dim_phys) if ph & names), None)
            if level.kind in ("fixed", "include") and hit is None:
                raise Unsupported(f"level dimension {d!r} is not a dimension of the query")
            if hit is not None:
                idx.append(hit)
        if level.kind == "include":
            raise Unsupported("include levels (finer than the query) are not compiled yet")
        if level.kind == "exclude":
            idx = [i for i in range(len(dims)) if i not in idx]
        return _Leaf(a, home, "fixed", tuple(sorted(set(idx))), level.filters == "ignore")

    def compile(self, measures: list[str], dims: list[FieldUse], filters: list[FilterSpec]) -> str:
        if not measures:
            raise Unsupported("no measures")
        exprs, leaves = self.leaves(measures, dims)

        groups: dict[tuple, list[_Leaf]] = {}
        for lf in leaves:
            groups.setdefault((lf.home, lf.level_kind, lf.level_dims, lf.ignore_filters), []).append(lf)

        ctes, report_ctes, level_ctes = [], [], []
        for n, ((home, kind, dim_idx, ignore), group) in enumerate(groups.items()):
            name = f"c{n}"
            ctes.append(self.cte(name, home, [dims[i] for i in dim_idx], group,
                                 [] if ignore else filters))
            for k, lf in enumerate(group):
                lf.alias = f"{name}.a{k}"
            (report_ctes if kind == "report" and len(dim_idx) == len(dims) else level_ctes).append((name, dim_idx))

        if not report_ctes:  # only level aggregates: the spine comes from the query grain of the first home
            home = leaves[0].home
            ctes.append(self.cte("cs", home, dims, [], filters))
            report_ctes.append(("cs", tuple(range(len(dims)))))

        if dims:
            spine = " UNION ".join(f"SELECT {', '.join(f'd{i}' for i in range(len(dims)))} FROM {c}"
                                   for c, _ in report_ctes)
        else:
            spine = "SELECT 1 AS one"
        ctes.append(f"spine AS ({spine})")
        joins = []
        for c, idx in report_ctes + level_ctes:
            on = " AND ".join(f"(s.d{i} = {c}.d{j} OR (s.d{i} IS NULL AND {c}.d{j} IS NULL))"
                              for j, i in enumerate(idx)) or "1 = 1"
            joins.append(f"LEFT JOIN {c} ON {on}")
        leaf_alias = {id(lf.agg): lf.alias for lf in leaves}
        selects = [f"s.d{i} AS d{i}" for i in range(len(dims))]
        selects += [f"{self.sql(e, {}, leaf_alias)} AS m{k}" for k, e in enumerate(exprs)]
        order = f" ORDER BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else ""
        return (f"WITH {', '.join(ctes)} SELECT {', '.join(selects)} FROM spine s {' '.join(joins)}{order}")

    def cte(self, name: str, home: str, dims: list[FieldUse], group: list[_Leaf], filters: list[FilterSpec]) -> str:
        tree = self.tree(home)
        dim_exprs = [self.dim_expr(d, home, tree) for d in dims]
        preds: list[Expr] = []
        for mf in self.m.filters:
            e = self.expand(mf.expr)
            if all(t == home or t in tree for t in self.tables_of(e)):
                preds.append(e)
            else:
                self.assumptions.append(f"model filter not applied to {home}: tables not reachable")
        for f in filters:
            e = self.filter_expr(f, home, tree)
            if e is None:
                self.assumptions.append(f"filter on {f.field} not applied to {home}: not reachable")
            else:
                preds.append(e)
        used = {home}
        for x in dim_exprs + preds + [lf.agg for lf in group]:
            used.update(self.tables_of(x))
        path = self.joins_for(home, used - {home}, tree)
        aliases = {home: "t0"}
        from_sql = f"{self.relation(home)} AS t0"
        for k, (j, parent, t) in enumerate(path, 1):
            aliases[t] = f"t{k}"
        for j, parent, t in path:
            keys = []
            for lc, rc in j.keys:
                a, b = (lc, rc) if parent == j.left else (rc, lc)
                keys.append(f"{self.column(Col(table=parent, column=a), aliases)} = "
                            f"{self.column(Col(table=t, column=b), aliases)}")
            from_sql += f" {self.join_type(j, parent)} {self.relation(t)} AS {aliases[t]} ON {' AND '.join(keys)}"
        cols = [f"{self.dim_sql(d, e, aliases)} AS d{i}" for i, (d, e) in enumerate(zip(dims, dim_exprs))]
        cols += [f"{self.sql(lf.agg, aliases)} AS a{k}" for k, lf in enumerate(group)]
        if not cols:
            cols = ["1 AS one"]
        where = f" WHERE {' AND '.join(self.sql(p, aliases) for p in preds)}" if preds else ""
        group_by = f" GROUP BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else ""
        if not group and dims:
            return f"{name} AS (SELECT DISTINCT {', '.join(cols)} FROM {from_sql}{where})"
        return f"{name} AS (SELECT {', '.join(cols)} FROM {from_sql}{where}{group_by})"


def _outside_aggregates(e: Expr) -> list[str]:
    if isinstance(e, Agg):
        return []
    if isinstance(e, Col):
        return [f"{e.table}::{e.column}"]
    from inspector_contract.expr import children

    return [x for c in children(e) for x in _outside_aggregates(c)]
