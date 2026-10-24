"""Everything the checks need, computed once per run."""

from __future__ import annotations

from dataclasses import dataclass, field

from inspector_contract import MigrationPackage, PhysicalTable, SemanticModel
from ..lineage_tracer.resolve import Resolved, home_table, resolve_model
from ..join_analyzer.graph import JoinGraph, KeyCatalog, model_graph

from ..counterpart_finder.matching import FieldMatch, match_fields, pick_models


@dataclass
class Context:
    package: MigrationPackage
    ts: SemanticModel
    st: SemanticModel
    ts_res: dict[str, Resolved]
    st_res: dict[str, Resolved]
    matches: list[FieldMatch]
    ts_graph: JoinGraph
    st_graph: JoinGraph
    catalog: KeyCatalog
    used_in_content: set[str] = field(default_factory=set)
    ignored: dict[str, str] = field(default_factory=dict)  # ThoughtSpot field -> reason (mapping ignore)

    @property
    def pairs(self) -> list[FieldMatch]:
        return [m for m in self.matches if m.ts and m.st]

    def home(self, resolved: Resolved) -> PhysicalTable | None:
        return home_table(resolved)


def _content_fields(pkg: MigrationPackage) -> set[str]:
    used = set()
    for d in pkg.thoughtspot.dashboards:
        used.update(f.field for f in d.filters)
        for v in d.visualizations:
            q = v.query
            used.update(u.field for u in q.dimensions + q.measures)
            used.update(f.field for f in q.filters)
            used.update(s[0] for s in q.sort)
    return used


def build(pkg: MigrationPackage) -> Context:
    ts, st = pick_models(pkg)
    ts_res, st_res = resolve_model(ts), resolve_model(st)
    ignored = {}
    if pkg.mapping:
        for e in pkg.mapping.ignore:
            model, _, name = e.thoughtspot.rpartition("::")
            if not model or model == ts.name:
                ignored[name] = e.reason
    matches = match_fields(pkg, ts, st, ts_res, st_res, ignored)
    ts_graph, st_graph = model_graph(ts), model_graph(st)
    catalog = KeyCatalog()
    catalog.absorb(ts_graph)
    catalog.absorb(st_graph)
    return Context(pkg, ts, st, ts_res, st_res, matches, ts_graph, st_graph, catalog,
                   used_in_content=_content_fields(pkg), ignored=ignored)
