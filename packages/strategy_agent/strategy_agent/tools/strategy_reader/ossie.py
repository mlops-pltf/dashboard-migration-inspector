"""Apache Ossie (formerly OSI) YAML -> IR.

Ossie is lossy by design: metric levels, hierarchies, join types, composite join keys, metric
conditions, VLDB properties and Apply* expressions are not carried. The model records this in
``lossy`` so checks that depend on them come back UNVERIFIED instead of passing silently.
"""

from __future__ import annotations

from pathlib import Path

from sqlglot.errors import ParseError as SqlParseError

from inspector_contract import Col, Expr, Field, Join, LogicalTable, ParseIssue, PhysicalTable, Relation, SemanticModel
from inspector_contract.ir import Opaque
from ..sql_analyzer.convert import parse_expression, to_ir
from ..sql_analyzer.lineage import analyze
from ..common.yamlio import Locator, load_yaml

LOSSY = ("metric levels", "hierarchies", "join types", "composite join keys", "metric conditions",
         "VLDB properties", "Apply* expressions", "previous-period and to-date functions")


def _ansi(entry: dict) -> str | None:
    for d in ((entry.get("expression") or {}).get("dialects") or []):
        if str(d.get("dialect", "")).upper() == "ANSI_SQL":
            return str(d.get("expression"))
    return None


def _parse(text: str, default_table: str | None) -> Expr:
    try:
        node = parse_expression(text, dialect="postgres")
    except SqlParseError as exc:
        return Opaque(dialect="ansi", text=text, reason=str(exc))
    return to_ir(node, lambda table, column: Col(table=(table or default_table or "?").lower(), column=column.lower()))


def load(path: Path, root: Path | None = None) -> tuple[SemanticModel, list[ParseIssue]]:
    doc = load_yaml(path)
    loc = Locator(path, root)
    sm = (doc.get("semantic_model") or [{}])[0]
    issues: list[ParseIssue] = []

    tables = []
    for i, ds in enumerate(sm.get("datasets") or []):
        name = str(ds.get("name"))
        source = str(ds.get("source", name))
        if source.strip().lower().startswith(("select", "with")):  # custom SQL dataset: source holds the SQL
            relation = Relation(kind="sql", sql=source, lineage=analyze(source))
        else:
            parts = source.lower().split(".")
            relation = Relation(kind="physical",
                                table=PhysicalTable(namespace=parts[-2] if len(parts) > 1 else "public", table=parts[-1]),
                                database=parts[-3] if len(parts) > 2 else None)
        tables.append(LogicalTable(id=name, name=name, relation=relation,
                                   source=loc.ref(f"semantic_model[0].datasets[{i}]", sm["datasets"], i)))

    fields = []
    for i, ds in enumerate(sm.get("datasets") or []):
        for k, f in enumerate(ds.get("fields") or []):
            text = _ansi(f)
            expr = _parse(text, str(ds.get("name"))) if text else Opaque(dialect="ansi", text=str(f.get("name")),
                                                                          reason="no ANSI_SQL expression")
            fields.append(Field(id=f"ossie:{sm.get('name')}:{f.get('name')}", name=str(f.get("name")),
                                kind="ATTRIBUTE", expr=expr, object_type="ossie:field",
                                source=loc.ref(f"semantic_model[0].datasets[{i}].fields[{k}]", ds["fields"], k)))

    for i, m in enumerate(sm.get("metrics") or []):
        text = _ansi(m)
        src = loc.ref(f"semantic_model[0].metrics[{i}]", sm["metrics"], i)
        if text:
            expr = _parse(text, None)
        else:
            expr = Opaque(dialect="ansi", text=str(m.get("name")), reason="metric exported without an expression")
            issues.append(ParseIssue(severity="warning", message=f"metric {m.get('name')!r} has no expression "
                                     "(unsupported pattern in Ossie export)", source=src))
        fields.append(Field(id=f"ossie:{sm.get('name')}:{m.get('name')}", name=str(m.get("name")), kind="MEASURE",
                            expr=expr, object_type="ossie:metric", description=str(m.get("description", "") or ""),
                            source=src))

    joins = []
    for i, r in enumerate(sm.get("relationships") or []):
        keys = tuple(zip((str(c) for c in r.get("from_columns") or []), (str(c) for c in r.get("to_columns") or [])))
        joins.append(Join(id=str(r.get("name", f"rel{i}")), left=str(r.get("from")), right=str(r.get("to")),
                          type="UNKNOWN", keys=keys, cardinality="N:1", origin="declared",
                          source=loc.ref(f"semantic_model[0].relationships[{i}]", sm["relationships"], i)))

    model = SemanticModel(id=f"ossie:{sm.get('name')}", name=str(sm.get("name")), tool="strategy", kind="ossie",
                          tables=tuple(tables), joins=tuple(joins), fields=tuple(fields), lossy=LOSSY,
                          settings={"join_types_known": False}, description=str(sm.get("description", "") or ""),
                          source=loc.ref("semantic_model[0]", sm))
    return model, issues
