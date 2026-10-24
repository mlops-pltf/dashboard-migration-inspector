"""Strategy (Mosaic) emitters.

Shapes follow the official REST API samples (github.com/MicroStrategy/rest-api-docs):
  * model YAML export   POST /api/model/dataModels/{id}/export
  * REST JSON           GET  /api/model/dataModels/{id}[/tables|/attributes|/factMetrics|/metrics|/hierarchy]
  * Freeform SQL table  physicalTable.type = "sql" + sqlStatement (modeling "create a freeform SQL table")
  * model SQL view      GET  /api/v2/cubes/{dataModelId}/sqlView

Synthetic assumptions (to replace once a real export is available; see synthetic/README.md):
  A1  dashboard definition JSON shape
  A2  lookup-style join semantics declared via advancedProperties "[Joins].[Preserve all final pass result elements]"
  A5  Apache Ossie YAML top-level layout
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import CONNECTION, DB, SCHEMA
from .domain import ALIAS_TO_TABLE, TABLE_INFO, Liveboard, SemanticModel
from .ids import fingerprint, strategy_id
from .sqlgen import FreeformSql, fq
from .warehouse import TABLES

LOCALE = "en-US"
CREATED = "2026-10-01T09:00:00.000Z"
DATASOURCE = {"objectId": strategy_id("datasource", CONNECTION), "subType": "db_role", "name": CONNECTION}
PRESERVE_OUTER = {"name": "[Joins].[Preserve all final pass result elements]",
                  "value": "preserve_all_final_pass_result_elements_outer_join"}

_VIZ_TYPES = {"COLUMN": "vertical_bar", "BAR": "horizontal_bar", "LINE": "line", "TABLE": "grid", "KPI": "kpi"}
_PLACEHOLDER = re.compile(r"\{([a-z]{1,2})\.([a-z_]+)\}")


@dataclass(frozen=True)
class StrategySpec:
    """What the Strategy developer built for one ThoughtSpot model."""

    name: str
    model: SemanticModel  # Strategy-side semantics (scenarios mutate this copy)
    style: str = "lookup"  # lookup | ffsql
    renames: dict[str, str] = field(default_factory=dict)  # ThoughtSpot field name -> Strategy object name
    ffsql: FreeformSql | None = None
    extra_attributes: tuple[tuple[str, str], ...] = ()  # (name, Freeform SQL output column)
    extra_metrics: tuple[tuple[str, str], ...] = ()  # (name, expression text)
    content: tuple[Liveboard, ...] = ()
    description: str = ""

    def sname(self, ts_name: str) -> str:
        return self.renames.get(ts_name, ts_name)


def _data_type(sql_type: str) -> dict:
    base = sql_type.split("(")[0]
    if base == "integer":
        return {"type": "integer", "precision": 4, "scale": 0}
    if base == "bigint":
        return {"type": "integer", "precision": 8, "scale": 0}
    if base == "decimal":
        p, s = sql_type[sql_type.index("(") + 1:-1].split(",")
        return {"type": "decimal", "precision": int(p), "scale": int(s)}
    if base == "varchar":
        return {"type": "utf8_char", "precision": int(sql_type[sql_type.index("(") + 1:-1]), "scale": 0}
    if base == "date":
        return {"type": "date", "precision": 10, "scale": 0}
    return {"type": "time_stamp", "precision": 26, "scale": 6}


def _display_format(sql_type: str) -> str:
    base = sql_type.split("(")[0]
    return {"integer": "number", "bigint": "number", "decimal": "number", "date": "date",
            "timestamp": "date_time"}.get(base, "text")


class _Builder:
    def __init__(self, spec: StrategySpec):
        self.spec = spec
        self.m = spec.model
        self.ids: dict[tuple[str, str], str] = {}

    def oid(self, kind: str, name: str) -> str:
        key = (kind, name)
        if key not in self.ids:
            self.ids[key] = strategy_id(self.spec.name, kind, name)
        return self.ids[key]

    def ref(self, kind: str, name: str, sub_type: str | None = None) -> dict:
        return {"objectId": self.oid(kind, name), "subType": sub_type or kind, "name": name}

    def info(self, kind: str, name: str, sub_type: str | None = None, description: str = "") -> dict:
        out = {"primaryLocale": LOCALE, "objectId": self.oid(kind, name), "subType": sub_type or kind, "name": name}
        if description:
            out["description"] = description
        return out

    # ---- tables --------------------------------------------------------------------------
    def logical_tables(self) -> list[tuple[str, dict]]:
        """(logical table name, physicalTable) pairs."""
        if self.spec.style == "ffsql":
            ff = self.spec.ffsql
            columns = []
            for out, src in ff.columns:
                alias, column = src.split(".")
                columns.append((out, TABLES[TABLE_INFO[ALIAS_TO_TABLE[alias]][0]].col(column).sql_type))
            name = f"{self.m.name} SQL"
            return [(name, {
                "information": self.info("physical_table", name),
                "type": "sql",
                "sqlStatement": ff.sql,
                "columns": [self._column(name, c, t) for c, t in columns],
            })]
        out = []
        for t in self.m.tables:
            physical = TABLE_INFO[t][0]
            sql = filtered_table_sql(self.m, t)
            if sql:  # model filter: the developer wraps the fact table in a one-table Freeform SQL
                out.append((physical, {
                    "information": self.info("physical_table", physical),
                    "type": "sql",
                    "sqlStatement": sql,
                    "columns": [self._column(physical, c.name, c.sql_type) for c in TABLES[physical].columns],
                }))
                continue
            out.append((physical, {
                "information": self.info("physical_table", physical),
                "type": "normal",
                "namespace": SCHEMA,
                "tableName": physical,
                "columns": [self._column(physical, c.name, c.sql_type) for c in TABLES[physical].columns],
            }))
        return out

    def _column(self, table: str, column: str, sql_type: str) -> dict:
        return {"information": self.info("column", f"{table}.{column}", "column") | {"name": column},
                "dataType": _data_type(sql_type), "columnName": column}

    # ---- attributes ----------------------------------------------------------------------
    def attribute_placements(self) -> dict[str, list[tuple[str, str]]]:
        """Strategy attribute name -> [(column expression, logical table)] (the join keys span several tables)."""
        out: dict[str, list[tuple[str, str]]] = {}
        for f in self.m.fields:
            if f.kind != "ATTRIBUTE":
                continue
            if self.spec.style == "ffsql":
                alias, column = _PLACEHOLDER.search(f.sql).groups()
                out[self.spec.sname(f.name)] = [(self.spec.ffsql.colmap[f"{alias}.{column}"], f"{self.m.name} SQL")]
                continue
            places = [(f.column, TABLE_INFO[f.table][0])]
            changed = True
            while changed:  # follow join keys transitively (product_id on lines, returns and product)
                changed = False
                for j in self.m.joins:
                    for lc, rc in j.keys:
                        a = (lc, TABLE_INFO[j.source][0])
                        b = (rc, TABLE_INFO[j.destination][0])
                        for x, y in ((a, b), (b, a)):
                            if x in places and y not in places:
                                places.append(y)
                                changed = True
            out[self.spec.sname(f.name)] = places
        for name, column in self.spec.extra_attributes:
            out[name] = [(column, f"{self.m.name} SQL")]
        return out

    def _lookup_table(self, places: list[tuple[str, str]]) -> str:
        if len(places) == 1:
            return places[0][1]
        dims = {TABLE_INFO[j.destination][0] for j in self.m.joins}
        return next((t for _, t in places if t.startswith("dim_")), next((t for _, t in places if t in dims), places[0][1]))

    def _sql_type(self, table: str, column: str) -> str:
        if self.spec.style == "ffsql":
            src = dict(self.spec.ffsql.columns)[column]
            alias, col = src.split(".")
            return TABLES[TABLE_INFO[ALIAS_TO_TABLE[alias]][0]].col(col).sql_type
        return TABLES[table].col(column).sql_type

    def attributes(self) -> list[dict]:
        out = []
        descriptions = {self.spec.sname(f.name): f.description for f in self.m.fields}
        for name, places in self.attribute_placements().items():
            lookup = self._lookup_table(places)
            sql_type = self._sql_type(places[0][1], places[0][0])
            grouped: dict[str, list[str]] = {}
            for column, table in places:
                grouped.setdefault(column, []).append(table)
            form_id = self.oid("form", f"{name} ID")
            out.append({
                "information": self.info("attribute", name, description=descriptions.get(name, "")),
                "forms": [{
                    "id": form_id, "name": "ID", "category": "ID", "type": "system",
                    "displayFormat": _display_format(sql_type), "dataType": _data_type(sql_type),
                    "expressions": [{"expression": {"text": column},
                                     "tables": [self.ref("table", t, "logical_table") for t in tables]}
                                    for column, tables in grouped.items()],
                    "alias": places[0][0],
                    "lookupTable": self.ref("table", lookup, "logical_table"),
                }],
                "attributeLookupTable": self.ref("table", lookup, "logical_table"),
                "keyForm": {"id": form_id, "name": "ID"},
                "displays": {"reportDisplays": [{"id": form_id, "name": "ID"}],
                             "browseDisplays": [{"id": form_id, "name": "ID"}]},
                "sorts": {},
                "nonAggregatable": False,
                "autoDetectLookupTable": True,
            })
        return out

    def hierarchy(self) -> dict:
        placements = self.attribute_placements()
        keys = {n: self._lookup_table(p) for n, p in placements.items() if len(p) > 1}
        rels, isolated = [], []
        for name, places in placements.items():
            if name in keys:
                continue
            table = places[0][1]
            child = next((k for k, lookup in keys.items() if lookup == table), None)
            if child is None:
                isolated.append(self.ref("attribute", name))
                continue
            rels.append({"parent": self.ref("attribute", name), "child": self.ref("attribute", child),
                         "relationshipTable": self.ref("table", table, "logical_table"),
                         "relationshipType": "one_to_many"})
        return {"relationships": rels, "isolatedAttributes": isolated}

    # ---- metrics -------------------------------------------------------------------------
    def fact_metrics(self) -> list[dict]:
        out = []
        for f in self.m.fields:
            if f.kind != "MEASURE" or f.st.kind != "fact_metric":
                continue
            name = self.spec.sname(f.name)
            if self.spec.style == "ffsql":
                text = f.st.expr
                for column in f.st.columns:
                    text = re.sub(rf"\b{column}\b", self.spec.ffsql.colmap[f"{f.home}.{column}"], text)
                table = f"{self.m.name} SQL"
                sql_type = self._sql_type(table, self.spec.ffsql.colmap[f"{f.home}.{f.st.columns[0]}"])
            else:
                text = f.st.expr
                table = TABLE_INFO[ALIAS_TO_TABLE[f.home]][0]
                sql_type = TABLES[table].col(f.st.columns[0]).sql_type
            props = [{"name": "UseLookupForAttributes", "value": {"type": "boolean", "value": "false"}}]
            if f.st.distinct:
                props.append({"name": "Distinct", "value": {"type": "boolean", "value": "true"}})
            if f.st.level:
                units = [{"dimtyUnitType": "attribute", "target": self.ref("attribute", self.spec.sname(a)),
                          "aggregation": "normal", "filtering": "apply", "groupBy": True} for a in f.st.level]
            else:
                units = [{"dimtyUnitType": "report_base_level", "aggregation": "normal", "filtering": "apply",
                          "groupBy": True}]
            data_type = {"type": "integer", "precision": 8, "scale": 0} if f.st.function == "count" \
                else _data_type(sql_type)
            out.append({
                "information": self.info("fact_metric", name, description=f.description),
                "fact": {"dataType": data_type,
                         "expressions": [{"expression": {"text": text},
                                          "tables": [self.ref("table", table, "logical_table")]}]},
                "function": f.st.function,
                "functionProperties": props,
                "dimty": {"dimtyUnits": units, "excludeAttribute": False, "allowAddingUnit": not f.st.level},
                "format": _number_format(f.st.function),
                "semanticRole": "fixed",
            })
        return out

    def metrics(self) -> list[dict]:
        out = []
        entries = [(self.spec.sname(f.name), f.st.expr, f.description) for f in self.m.fields
                   if f.kind == "MEASURE" and f.st.kind == "metric"]
        entries += [(n, e, "") for n, e in self.spec.extra_metrics]
        for name, expr, desc in entries:
            text = re.sub(r"\[([^\]]+)\]", lambda m: f"[{self.spec.sname(m.group(1))}]", expr)
            out.append({
                "information": self.info("metric", name, description=desc),
                "expression": {"text": text},
                "formulaJoinType": "default",
                "smartTotal": "decomposable_false",
                "dataType": {"type": "reserved", "precision": 0, "scale": 0},
                "format": {"header": [], "values": []},
                "pushDownBehavior": "default",
            })
        return out

    # ---- assembly ------------------------------------------------------------------------
    def model_doc(self) -> dict:
        tables = self.logical_tables()
        attributes = self.attributes()
        fact_metrics = self.fact_metrics()
        placements = self.attribute_placements()
        table_entries = []
        for name, physical in tables:
            on_table = [a for a, places in placements.items() if any(t == name for _, t in places)]
            facts = [fm["information"]["name"] for fm in fact_metrics
                     if fm["fact"]["expressions"][0]["tables"][0]["name"] == name]
            entry = {"information": self.info("table", name, "logical_table"), "physicalTable": physical,
                     "primaryDataSource": DATASOURCE}
            if on_table:
                entry["attributes"] = [{"information": self.ref("attribute", a)} for a in on_table]
            if facts:
                entry["factMetrics"] = [{"information": self.ref("fact_metric", n)} for n in facts]
            entry["refreshPolicy"] = "replace"
            table_entries.append(entry)

        doc = {
            "information": self.info("model", self.spec.name, "report_emma_cube",
                                     self.spec.description or self.m.description),
            "dataServeMode": "in_memory",
            "enableWrangleRecommendations": True,
            "enableAutoHierarchyRelationships": True,
            "sampling": {"type": "first", "rowCount": 1000},
            "partition": {"mode": "automatic", "number": 4},
            "autoJoin": True,
        }
        if self.spec.style == "lookup":
            doc["advancedProperties"] = [PRESERVE_OUTER]
        metrics = self.metrics()
        doc |= {
            "tables": table_entries,
            "attributes": attributes,
            "factMetrics": fact_metrics,
            "metrics": metrics,
            "hierarchy": self.hierarchy(),
            "securityFilters": [],
            "folders": [
                {"subType": "folder", "name": "Attributes", "primaryLocale": LOCALE,
                 "contents": [self.ref("attribute", a["information"]["name"]) for a in attributes]},
                {"subType": "folder", "name": "Metrics", "primaryLocale": LOCALE,
                 "contents": [self.ref("fact_metric", m["information"]["name"]) for m in fact_metrics]
                 + [self.ref("metric", m["information"]["name"]) for m in metrics]},
            ],
        }
        return doc


def filtered_table_sql(model: SemanticModel, table: str) -> str | None:
    """SELECT over one table with the model filters that live on it, or None if it has none."""
    preds = []
    for flt in model.filters:
        f = model.field(flt.field)
        if f.table != table:
            continue
        col = f"{TABLE_INFO[table][1]}.{f.column}"
        if flt.oper == "not in" and len(flt.values) == 1:
            preds.append(f"{col} <> '{flt.values[0]}'")
        else:
            preds.append(f"{col} {flt.oper.upper()} (" + ", ".join(f"'{v}'" for v in flt.values) + ")")
    if not preds:
        return None
    physical, alias = TABLE_INFO[table]
    cols = ",\n".join(f"    {alias}.{c.name}" for c in TABLES[physical].columns)
    return f"SELECT\n{cols}\nFROM {fq(physical)} {alias}\nWHERE " + "\n  AND ".join(preds)


def _number_format(function: str) -> dict:
    fmt = "#,##0" if function == "count" else "#,##0.00"
    return {"header": [], "values": [{"type": "number_category", "value": "0"},
                                     {"type": "number_format", "value": fmt},
                                     {"type": "number_thousand_separator", "value": "true"}]}


def build_model(spec: StrategySpec) -> tuple[dict, _Builder]:
    b = _Builder(spec)
    return b.model_doc(), b


def yaml_export(model: dict, dump) -> str:
    body = {"version": "1.0", "metadataLocale": LOCALE, "dateNumberLocale": LOCALE, "model": [model]}
    text = dump(body)
    return text + f"fingerprint: \"{fingerprint(text)}\"\n"


def _stamp(info: dict) -> dict:
    return {"dateCreated": CREATED, "dateModified": CREATED,
            "versionId": strategy_id("version", info["objectId"]), "acg": 255} | info


def rest_json(model: dict) -> dict[str, dict]:
    """The per-object REST responses for the same model."""
    def stamped(objs):
        return [o | {"information": _stamp(o["information"])} for o in objs]

    settings = {k: v for k, v in model.items() if k not in
                ("tables", "attributes", "factMetrics", "metrics", "hierarchy", "securityFilters", "folders")}
    settings["information"] = _stamp(settings["information"])
    settings["readOnly"] = False

    def page(key, objs):
        return {"offset": 0, "limit": -1, "total": len(objs), key: stamped(objs)}

    return {
        "dataModel.json": settings,
        "tables.json": page("tables", model["tables"]),
        "attributes.json": page("attributes", model["attributes"]),
        "factMetrics.json": page("factMetrics", model["factMetrics"]),
        "metrics.json": page("metrics", model["metrics"]),
        "hierarchy.json": model["hierarchy"],
    }


def ossie_yaml(spec: StrategySpec) -> dict:
    """Apache Ossie export: ANSI SQL only. Levels, hierarchies, VLDB and Apply* functions are lost."""
    m = spec.model

    def ansi(text: str) -> str:
        return _PLACEHOLDER.sub(lambda mm: f"{TABLE_INFO[ALIAS_TO_TABLE[mm.group(1)]][0]}.{mm.group(2)}", text)

    datasets = []
    for t in m.tables:
        physical = TABLE_INFO[t][0]
        fields = [{"name": spec.sname(f.name),
                   "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": f.column}]}}
                  for f in m.fields if f.kind == "ATTRIBUTE" and f.table == t]
        datasets.append({"name": physical, "source": filtered_table_sql(m, t) or fq(physical),
                         "primary_key": list(TABLES[physical].key),
                         "fields": fields})
    relationships = [{"name": f"{TABLE_INFO[j.source][0]}_to_{TABLE_INFO[j.destination][0]}",
                      "from": TABLE_INFO[j.source][0], "to": TABLE_INFO[j.destination][0],
                      "from_columns": [j.keys[0][0]], "to_columns": [j.keys[0][1]]} for j in m.joins]
    metrics = []
    for f in m.fields:
        if f.kind != "MEASURE":
            continue
        entry = {"name": spec.sname(f.name)}
        if "ApplySimple" not in f.st.expr:
            entry["expression"] = {"dialects": [{"dialect": "ANSI_SQL", "expression": ansi(f.sql)}]}
        if f.description:
            entry["description"] = f.description
        metrics.append(entry)
    return {"version": "1.0", "semantic_model": [{
        "name": spec.name, "description": spec.description or m.description,
        "datasets": datasets, "relationships": relationships, "metrics": metrics}]}


def dashboard(spec: StrategySpec, builder: _Builder, lb: Liveboard) -> dict:
    model_id = builder.oid("model", spec.name)
    measure_kind = {spec.sname(f.name): ("fact_metric" if f.st.kind == "fact_metric" else "metric")
                    for f in spec.model.fields if f.kind == "MEASURE"}
    measure_kind |= {n: "metric" for n, _ in spec.extra_metrics}

    def obj(ts_name: str) -> dict:
        name = spec.sname(ts_name)
        if name in measure_kind:
            return {"id": builder.oid(measure_kind[name], name), "name": name, "type": "metric"}
        return {"id": builder.oid("attribute", name), "name": name, "type": "attribute"}

    vizs = []
    for i, v in enumerate(lb.visualizations, 1):
        definition = {"grid": {"rows": [obj(d) for d in v.dims], "columns": [obj(m) for m in v.measures]}}
        if v.sort:
            definition["sorts"] = [obj(v.sort[0]) | {"order": "descending" if v.sort[1] == "DESC" else "ascending"}]
        if v.top_n:
            definition["limit"] = {"type": "top", "count": v.top_n, "by": obj(v.sort[0]) if v.sort else None}
        vizs.append({"key": f"W{i}", "name": v.title or v.name, "visualizationType": _VIZ_TYPES[v.chart],
                     "datasetId": model_id, "definition": definition})

    filters = []
    for i, flt in enumerate(lb.filters, 1):
        target = obj(flt.field)
        filters.append({
            "key": f"F{i}", "name": target["name"], "source": target,
            "selector": {"type": "element_list", "multiSelection": True},
            "currentSelection": {"selectionStatus": "excluded" if flt.oper == "not in" else "included",
                                 "elements": [{"id": f"h{v};{target['id']}", "name": v} for v in flt.values]},
            "targets": "all_visualizations",
        })
    return {
        "id": strategy_id(spec.name, "dashboard", lb.name),
        "name": lb.name,
        "type": "dashboard",
        "datasets": [{"id": model_id, "name": spec.name, "type": "data_model"}],
        "chapters": [{"key": "C1", "name": lb.name, "filters": filters,
                      "pages": [{"key": "K1", "name": "Overview", "visualizations": vizs}]}],
    }


def model_sql_view(spec: StrategySpec) -> dict:
    header = f"[DB Instance: {CONNECTION}]\n\n"
    if spec.style == "ffsql":
        return {"sqlStatement": header + spec.ffsql.sql + "\n"}
    statements = []
    for i, t in enumerate(spec.model.tables, 1):
        physical = TABLE_INFO[t][0]
        sql = filtered_table_sql(spec.model, t)
        if sql:
            statements.append(sql)
            continue
        cols = ",\n\t".join(f'"a1{i}"."{c.name}" AS "{c.name}"' for c in TABLES[physical].columns)
        statements.append(f'select\t{cols}\nfrom\t"{DB}"."{SCHEMA}"."{physical}"\t"a1{i}"')
    return {"sqlStatement": header + "\n\n".join(statements) + "\n\n[Analytical engine calculation steps:\n]\n"}
