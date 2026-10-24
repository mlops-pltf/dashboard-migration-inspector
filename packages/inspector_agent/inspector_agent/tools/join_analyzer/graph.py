"""Join graphs over physical tables, the key catalog, and row-multiplication analysis for SQL relations.

Technology-neutral: the experts' IR states each join's type, cardinality and when it is applied (only when
needed, or to every query). Joins written inside a SQL-backed table are always applied.

The key catalog collects what the metadata says about uniqueness (N:1 => the one side's keys are unique, the
many side's are not). Fan-out / chasm analysis uses it to decide, per measure home, whether a join inside one SQL
multiplies rows: yes, no, or unknown (then data decides).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from inspector_contract import Col, PCol, PhysicalColumn, PhysicalTable, SemanticModel, SourceRef
from ..lineage_tracer.resolve import resolve_expr



@dataclass(frozen=True)
class Edge:
    left: PhysicalTable
    right: PhysicalTable
    type: str  # INNER | LEFT | RIGHT | FULL | CROSS | UNKNOWN
    keys: tuple[tuple[PhysicalColumn, PhysicalColumn], ...]  # (left column, right column)
    cardinality: str  # N:1 | 1:1 | 1:N | unknown
    origin: str  # declared | derived | sql
    always: bool  # applied to every query (Freeform SQL / non-progressive) vs only when needed
    name: str
    container: str | None = None  # logical table holding the SQL, for sql edges
    source: SourceRef | None = None

    @property
    def tables(self) -> frozenset[PhysicalTable]:
        return frozenset((self.left, self.right))

    def oriented(self, left: PhysicalTable) -> "Edge":
        """The same edge seen from ``left`` (keys and outer side swapped as needed)."""
        if self.left == left:
            return self
        swap = {"LEFT": "RIGHT", "RIGHT": "LEFT"}.get(self.type, self.type)
        card = {"N:1": "1:N", "1:N": "N:1"}.get(self.cardinality, self.cardinality)
        return Edge(self.right, self.left, swap, tuple((b, a) for a, b in self.keys), card, self.origin,
                    self.always, self.name, self.container, self.source)

    def key_set(self) -> frozenset[frozenset[PhysicalColumn]]:
        return frozenset(frozenset(pair) for pair in self.keys)


@dataclass
class JoinGraph:
    edges: list[Edge] = field(default_factory=list)

    def between(self, a: PhysicalTable, b: PhysicalTable) -> list[Edge]:
        return [e for e in self.edges if e.tables == frozenset((a, b))]


@dataclass
class KeyCatalog:
    unique: set[frozenset[PhysicalColumn]] = field(default_factory=set)
    non_unique: set[frozenset[PhysicalColumn]] = field(default_factory=set)
    evidence: dict[frozenset[PhysicalColumn], str] = field(default_factory=dict)

    def add(self, cols: tuple[PhysicalColumn, ...], unique: bool, why: str) -> None:
        key = frozenset(cols)
        (self.unique if unique else self.non_unique).add(key)
        self.evidence.setdefault(key, why)

    def is_unique(self, cols: tuple[PhysicalColumn, ...]) -> bool | None:
        key = frozenset(cols)
        if any(u <= key for u in self.unique):
            return True
        if key in self.non_unique:
            return False
        return None

    def absorb(self, graph: JoinGraph) -> None:
        for e in graph.edges:
            if not e.keys:
                continue
            left = tuple(a for a, _ in e.keys)
            right = tuple(b for _, b in e.keys)
            if e.cardinality in ("N:1", "1:1"):
                self.add(right, True, f"{e.origin} join {e.name}")
            if e.cardinality == "1:1":
                self.add(left, True, f"{e.origin} join {e.name}")
            if e.cardinality == "N:1":
                self.add(left, False, f"{e.origin} join {e.name}")
            if e.cardinality == "1:N":
                self.add(left, True, f"{e.origin} join {e.name}")
                self.add(right, False, f"{e.origin} join {e.name}")


def _pcol(model: SemanticModel, table_id: str, column: str) -> PhysicalColumn | None:
    expr, _ = resolve_expr(model, Col(table=table_id, column=column))
    return expr.column if isinstance(expr, PCol) else None


def _sql_edges(model: SemanticModel) -> list[Edge]:
    edges = []
    for t in model.tables:
        lin = t.relation.lineage
        if t.relation.kind != "sql" or lin is None:
            continue
        for j in lin.joins:
            if j.keys:
                left, right = j.keys[0][0].physical_table, j.keys[0][1].physical_table
            else:
                right = lin.sources.get(j.right)
                left = lin.sources.get(j.left[0]) if j.left else None
                if left is None or right is None:
                    continue
            edges.append(Edge(left, right, j.type, j.keys, "unknown", "sql", True, f"{t.name}: JOIN {right}",
                              container=t.id, source=t.source))
    return edges


def model_graph(model: SemanticModel) -> JoinGraph:
    """Joins of any model in the IR. The experts state the semantics: type, cardinality, and whether a join is
    applied only when needed or to every query. Joins written inside SQL tables are always applied."""
    edges = []
    for j in model.joins:
        keys = []
        for lc, rc in j.keys:
            a, b = _pcol(model, j.left, lc), _pcol(model, j.right, rc)
            if a and b:
                keys.append((a, b))
        if not keys:
            continue
        edges.append(Edge(keys[0][0].physical_table, keys[0][1].physical_table, j.type, tuple(keys),
                          j.cardinality, j.origin, j.applied == "always", j.id, source=j.source))
    return JoinGraph(edges + _sql_edges(model))


# ---------------------------------------------------------------------------------------------
# Row multiplication inside one SQL relation
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Multiplication:
    edge: Edge
    far_side: frozenset[PhysicalTable]  # tables on the other side of the edge from the measure's home
    far_keys: tuple[PhysicalColumn, ...]
    verdict: str  # safe | multiplies | unknown
    evidence: str


def row_multiplication(edges: list[Edge], home: PhysicalTable, catalog: KeyCatalog) -> list[Multiplication]:
    """For every join in a SQL relation: does it multiply the rows of ``home``?

    Joins form a tree over tables. Cutting an edge splits it in two; rows of ``home`` repeat when the
    far side's join columns are not unique.
    """
    out = []
    for e in edges:
        if not e.keys:
            out.append(Multiplication(e, frozenset(), (), "unknown", "join without equality keys"))
            continue
        near = _component(edges, home, exclude=e)
        if e.left in near and e.right not in near:
            far_keys, far_table = tuple(b for _, b in e.keys), e.right
        elif e.right in near and e.left not in near:
            far_keys, far_table = tuple(a for a, _ in e.keys), e.left
        else:
            continue
        far = _component(edges, far_table, exclude=e)
        unique = catalog.is_unique(far_keys)
        if unique is True:
            verdict, why = "safe", catalog.evidence.get(next(u for u in catalog.unique if u <= frozenset(far_keys)), "")
        elif unique is False:
            verdict, why = "multiplies", catalog.evidence.get(frozenset(far_keys), "")
        else:
            verdict, why = "unknown", "no metadata says these columns are unique"
        out.append(Multiplication(e, frozenset(far), far_keys, verdict,
                                  f"{', '.join(map(str, far_keys))}: {why}" if why else ", ".join(map(str, far_keys))))
    return out


def _component(edges: list[Edge], start: PhysicalTable, exclude: Edge) -> set[PhysicalTable]:
    seen, stack = {start}, [start]
    while stack:
        t = stack.pop()
        for e in edges:
            if e is exclude:
                continue
            for a, b in ((e.left, e.right), (e.right, e.left)):
                if a == t and b not in seen:
                    seen.add(b)
                    stack.append(b)
    return seen
