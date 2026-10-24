"""Every query that reaches the warehouse passes here first.

Accepted: exactly one read-only SELECT (CTEs, set operations allowed), reading only tables in the allowed schemas,
schema-qualified. Rejected: DDL/DML/COPY/UNLOAD/SELECT INTO, multiple statements, system catalogs, dangerous
functions. The SQL is transpiled from the source dialect (Redshift) to the warehouse dialect (Postgres locally),
and optionally wrapped with a row cap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_DENY_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Merge", "Into", "Grant", "Revoke",
    "TruncateTable", "Copy", "Use", "Set", "Transaction", "Commit", "Rollback", "LoadData", "Analyze",
) if hasattr(exp, n))
_DENY_FUNCTIONS = {"pg_sleep", "pg_sleep_for", "pg_sleep_until", "dblink", "dblink_exec", "pg_read_file",
                   "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export", "pg_terminate_backend",
                   "pg_cancel_backend", "set_config", "query_to_xml", "pg_reload_conf", "current_setting"}
_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def allowed_schemas() -> set[str]:
    return {s.strip().lower() for s in os.environ.get("WAREHOUSE_ALLOWED_SCHEMAS", "retail").split(",") if s.strip()}


def warehouse_dialect() -> str:
    return os.environ.get("WAREHOUSE_DIALECT", "postgres")


@dataclass
class Guarded:
    ok: bool
    sql: str = ""  # executable SQL in the warehouse dialect
    tables: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def guard(sql: str, *, source_dialect: str = "redshift", target_dialect: str | None = None,
          schemas: set[str] | None = None, max_rows: int | None = None) -> Guarded:
    target_dialect = target_dialect or warehouse_dialect()
    schemas = schemas if schemas is not None else allowed_schemas()
    try:
        statements = [s for s in sqlglot.parse(sql, read=source_dialect) if s is not None]
    except ParseError as exc:
        return Guarded(False, reasons=[f"cannot parse: {exc}"])
    if len(statements) != 1:
        return Guarded(False, reasons=[f"exactly one statement allowed, got {len(statements)}"])
    root = statements[0]
    if not isinstance(root, _QUERY_ROOTS):
        return Guarded(False, reasons=[f"only SELECT queries are allowed, got {type(root).__name__}"])

    reasons: list[str] = []
    for node in root.walk():
        if isinstance(node, _DENY_NODES):
            reasons.append(f"{type(node).__name__} is not allowed")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _DENY_FUNCTIONS:
                reasons.append(f"function {name} is not allowed")
    ctes = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables = []
    for t in root.find_all(exp.Table):
        name = t.name.lower()
        if not t.db and name in ctes:
            continue
        if not t.db:
            reasons.append(f"table {name} must be schema-qualified")
            continue
        schema = t.db.lower()
        if schema not in schemas:
            reasons.append(f"schema {schema} is not allowed (allowed: {sorted(schemas)})")
        tables.append(f"{schema}.{name}")
    if reasons:
        return Guarded(False, tables=tables, reasons=sorted(set(reasons)))

    out = root.sql(dialect=target_dialect)
    if max_rows is not None:
        out = f"SELECT * FROM ({out}) AS guarded LIMIT {int(max_rows) + 1}"
    return Guarded(True, sql=out, tables=sorted(set(tables)))
