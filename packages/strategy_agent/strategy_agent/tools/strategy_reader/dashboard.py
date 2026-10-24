"""Strategy dashboard definition JSON -> IR (shape is assumption A1 until confirmed with a real export)."""

from __future__ import annotations

from pathlib import Path

from inspector_contract import Dashboard, DashboardFilter, FieldUse, QuerySpec, Visualization
from ..common.yamlio import Locator, load_json

CHARTS = {"vertical_bar": "column", "horizontal_bar": "bar", "line": "line", "grid": "table", "kpi": "kpi",
          "pie": "pie", "area": "area"}


def load(path: Path, root: Path | None = None) -> Dashboard:
    doc = load_json(path)
    loc = Locator(path, root)
    vizs, filters = [], []
    for c, chapter in enumerate(doc.get("chapters") or []):
        for f in chapter.get("filters") or []:
            sel = f.get("currentSelection") or {}
            op = "not in" if str(sel.get("selectionStatus", "included")) == "excluded" else "in"
            targets = f.get("targets")
            scope = tuple(targets) if isinstance(targets, list) else ()
            filters.append(DashboardFilter(field=str((f.get("source") or {}).get("name") or f.get("name")), op=op,
                                           values=tuple(str(e.get("name")) for e in sel.get("elements") or []),
                                           scope=scope))
        for p, page in enumerate(chapter.get("pages") or []):
            for v, viz in enumerate(page.get("visualizations") or []):
                d = viz.get("definition") or {}
                grid = d.get("grid") or {}
                sort = tuple((str(s.get("name")), "DESC" if str(s.get("order", "")).startswith("desc") else "ASC")
                             for s in d.get("sorts") or [])
                limit = d.get("limit") or {}
                query = QuerySpec(
                    dimensions=tuple(FieldUse(field=str(o.get("name"))) for o in grid.get("rows") or []),
                    measures=tuple(FieldUse(field=str(o.get("name"))) for o in grid.get("columns") or []),
                    sort=sort, top_n=int(limit["count"]) if limit.get("type") == "top" and limit.get("count") else None)
                vizs.append(Visualization(
                    id=str(viz.get("key", f"{c}.{p}.{v}")), name=str(viz.get("name", "")), query=query,
                    chart_type=CHARTS.get(str(viz.get("visualizationType", "")).lower(), "other"),
                    source=loc.ref(f"chapters[{c}].pages[{p}].visualizations[{v}]")))
    return Dashboard(id=f"mstr:dashboard:{doc.get('id', path.stem)}", name=str(doc.get("name", path.stem)),
                     tool="strategy", kind="dashboard",
                     models=tuple(str(ds.get("name")) for ds in doc.get("datasets") or []),
                     visualizations=tuple(vizs), filters=tuple(filters), source=loc.ref("", object_id=doc.get("id")))
