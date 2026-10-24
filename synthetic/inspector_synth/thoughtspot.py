"""ThoughtSpot TML emitters: Table, Worksheet, Model, Liveboard, Answer."""

from __future__ import annotations

from . import CONNECTION, DB, SCHEMA
from .domain import TABLE_COLUMNS, TABLE_INFO, TABLE_JOINS, Field, Liveboard, SemanticModel, Viz, table_column
from .ids import ts_guid
from .warehouse import TABLES

_TS_TYPES = {"integer": "INT32", "bigint": "INT64", "decimal": "DOUBLE", "varchar": "VARCHAR",
             "date": "DATE", "timestamp": "DATE_TIME"}


def _data_type(physical_table: str, column: str) -> str:
    sql_type = TABLES[physical_table].col(column).sql_type
    return _TS_TYPES[sql_type.split("(")[0]]


def _on(join, source_name: str | None = None, dest_name: str | None = None) -> str:
    src, dst = source_name or join.source, dest_name or join.destination
    return " AND ".join(
        f"[{src}::{table_column(join.source, lc)}] = [{dst}::{table_column(join.destination, rc)}]"
        for lc, rc in join.keys)


def table_tml(table: str) -> dict:
    physical, _ = TABLE_INFO[table]
    columns = []
    for name, column, agg in TABLE_COLUMNS[table]:
        props = {"column_type": "MEASURE" if agg else "ATTRIBUTE"}
        if agg:
            props["aggregation"] = agg
        props["index_type"] = "DONT_INDEX" if agg or column.endswith("_id") else "DEFAULT"
        columns.append({"name": name, "db_column_name": column.upper(), "properties": props,
                        "db_column_properties": {"data_type": _data_type(physical, column)}})
    doc = {"name": table, "db": DB.upper(), "schema": SCHEMA.upper(), "db_table": physical.upper(),
           "connection": {"name": CONNECTION, "fqn": ts_guid("connection", CONNECTION)},
           "columns": columns}
    joins = [j for j in TABLE_JOINS if j.source == table]
    if joins:
        doc["joins_with"] = [{"name": j.name, "destination": {"name": j.destination, "fqn": ts_guid("table", j.destination)},
                              "on": _on(j), "type": j.type, "is_one_to_one": j.cardinality == "ONE_TO_ONE"}
                             for j in joins]
    return {"guid": ts_guid("table", table), "table": doc}


def _path_id(table: str) -> str:
    return f"{table}_1"


def _formula_expr(f: Field, style: str) -> str:
    expr = f.formula
    for table in TABLE_INFO:
        expr = expr.replace("{" + table + "}", _path_id(table) if style == "worksheet" else table)
    return expr


def _column_entry(f: Field, ref_key: str, ref: str) -> dict:
    props = {"column_type": f.kind}
    if f.aggregation:
        props["aggregation"] = f.aggregation
    props["index_type"] = "DONT_INDEX" if f.kind == "MEASURE" else "DEFAULT"
    if f.synonyms:
        props["synonyms"] = list(f.synonyms)
    entry = {"name": f.name}
    if f.description:
        entry["description"] = f.description
    entry[ref_key] = ref
    entry["properties"] = props
    return entry


def _filters(model: SemanticModel, style: str) -> list[dict]:
    out = []
    for flt in model.filters:
        f = model.field(flt.field)
        table_ref = _path_id(f.table) if style == "worksheet" else f.table
        out.append({"column": [f"{table_ref}::{table_column(f.table, f.column)}"], "oper": flt.oper,
                    "values": list(flt.values)})
    return out


def worksheet_tml(model: SemanticModel) -> dict:
    formulas, columns = [], []
    for f in model.fields:
        if f.formula:
            fid = f"formula_{f.name}"
            formulas.append({"name": f.name, "expr": _formula_expr(f, "worksheet"), "id": fid})
            columns.append(_column_entry(f, "formula_id", fid))
        else:
            columns.append(_column_entry(f, "column_id", f"{_path_id(f.table)}::{table_column(f.table, f.column)}"))

    paths = []
    for table in model.tables:
        chain, current = [], table
        while True:  # walk back to the root fact along the model's joins
            j = next((j for j in model.joins if j.destination == current), None)
            if j is None:
                break
            chain.insert(0, j.name)
            current = j.source
        paths.append({"id": _path_id(table), "table": table,
                      "join_path": [{"join": chain}] if chain else [{}]})

    doc = {
        "name": model.name,
        "description": model.description,
        "tables": [{"name": t, "fqn": ts_guid("table", t)} for t in model.tables],
        "joins": [{"name": j.name, "source": j.source, "destination": j.destination, "type": j.type,
                   "is_one_to_one": j.cardinality == "ONE_TO_ONE"} for j in model.joins],
        "table_paths": paths,
        "formulas": formulas,
    }
    if model.filters:
        doc["filters"] = _filters(model, "worksheet")
    doc["worksheet_columns"] = columns
    doc["properties"] = {"is_bypass_rls": False, "join_progressive": model.progressive}
    return {"guid": ts_guid("worksheet", model.name), "worksheet": doc}


def model_tml(model: SemanticModel) -> dict:
    tables = []
    for t in model.tables:
        entry = {"name": t, "fqn": ts_guid("table", t)}
        joins = [j for j in model.joins if j.source == t]
        if joins:
            entry["joins"] = [{"with": j.destination, "on": _on(j), "type": j.type, "cardinality": j.cardinality}
                              for j in joins]
        tables.append(entry)
    formulas, columns = [], []
    for f in model.fields:
        if f.formula:
            fid = f"formula_{f.name}"
            formulas.append({"id": fid, "name": f.name, "expr": _formula_expr(f, "model")})
            columns.append(_column_entry(f, "formula_id", fid))
        else:
            columns.append(_column_entry(f, "column_id", f"{f.table}::{table_column(f.table, f.column)}"))
    doc = {"name": model.name, "description": model.description, "model_tables": tables, "formulas": formulas}
    if model.filters:
        doc["filters"] = _filters(model, "model")
    doc["columns"] = columns
    doc["properties"] = {"is_bypass_rls": False, "join_progressive": model.progressive}
    return {"guid": ts_guid("model", model.name), "model": doc}


def _answer_column_name(model: SemanticModel, name: str) -> str:
    f = model.field(name)
    if f.kind == "MEASURE" and f.aggregation == "SUM":
        return f"Total {name}"
    if f.kind == "MEASURE" and f.aggregation == "AVERAGE":
        return f"Average {name}"
    return name


def search_query(model: SemanticModel, viz: Viz) -> str:
    tokens = [f"[{d}]" for d in viz.dims] + [f"[{m}]" for m in viz.measures]
    if viz.top_n:
        tokens.append(f"top {viz.top_n}")
    if viz.sort:
        tokens.append(f"sort by [{viz.sort[0]}]" + (" descending" if viz.sort[1] == "DESC" else ""))
    return " ".join(tokens)


def answer_body(model: SemanticModel, viz: Viz) -> dict:
    cols = [_answer_column_name(model, n) for n in (*viz.dims, *viz.measures)]
    body = {
        "name": viz.title or viz.name,
        "tables": [{"id": model.name, "name": model.name, "fqn": ts_guid(model.style, model.name)}],
        "search_query": search_query(model, viz),
        "answer_columns": [{"name": c} for c in cols],
        "table": {"table_columns": [{"column_id": c, "show_headline": viz.chart == "KPI"} for c in cols],
                  "ordered_column_ids": cols},
    }
    if viz.chart == "TABLE":
        body["display_mode"] = "TABLE_MODE"
    else:
        dims = [_answer_column_name(model, d) for d in viz.dims]
        measures = [_answer_column_name(model, m) for m in viz.measures]
        body["chart"] = {"type": viz.chart, "chart_columns": [{"column_id": c} for c in cols],
                         "axis_configs": [{"x": dims, "y": measures}] if dims else [{"y": measures}]}
        body["display_mode"] = "CHART_MODE"
    return body


def _filter_entries(lb: Liveboard) -> list[dict]:
    return [{"column": [f.field], "oper": f.oper, "values": list(f.values)} for f in lb.filters]


def liveboard_tml(model: SemanticModel, lb: Liveboard) -> dict:
    vizs, tiles = [], []
    for i, viz in enumerate(lb.visualizations):
        vizs.append({"id": f"Viz_{i + 1}", "answer": answer_body(model, viz),
                     "viz_guid": ts_guid("viz", lb.name, viz.id)})
        tiles.append({"visualization_id": f"Viz_{i + 1}", "x": (i % 2) * 6, "y": (i // 2) * 5,
                      "height": 5, "width": 6})
    doc = {"name": lb.name, "visualizations": vizs}
    if lb.filters:
        doc["filters"] = _filter_entries(lb)
    doc["layout"] = {"tiles": tiles}
    return {"guid": ts_guid("liveboard", lb.name), "liveboard": doc}


def answer_tml(model: SemanticModel, lb: Liveboard) -> dict:
    return {"guid": ts_guid("answer", lb.name), "answer": answer_body(model, lb.visualizations[0])}
