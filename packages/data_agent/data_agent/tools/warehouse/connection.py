"""Read-only warehouse access. Every query the data agent runs goes through `fetch(sql)`: SQL in (already checked
by sql_guard: one read-only SELECT on allowed schemas), column names and rows out. The backend is plug-in:

  WAREHOUSE_BACKEND          postgres (default: a direct connection, Redshift speaks the same protocol)
                             | any name a host adds with register_backend(), e.g. a Redshift Data API backend
  WAREHOUSE_DSN              postgres backend: postgresql://user:pass@host:port/db
  WAREHOUSE_DIALECT          postgres | redshift   (SQL arrives in Redshift dialect and is transpiled to this)
  WAREHOUSE_TIMEOUT_S        statement timeout (default 60)
  WAREHOUSE_ALLOWED_SCHEMAS  comma-separated (default retail)
The postgres backend opens every session read-only with a statement timeout, on top of the database role's own
restrictions; another backend should use a read-only database user and enforce the timeout itself.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from decimal import Decimal

from collections.abc import Callable

_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")

Backend = Callable[[str], tuple[list[str], list[list]]]  # SQL -> (column names, rows of JSON-ready values)
_BACKENDS: dict[str, Backend] = {}


def register_backend(name: str, backend: Backend) -> None:
    """Add a way to reach the warehouse (selected with WAREHOUSE_BACKEND=<name>)."""
    _BACKENDS[name] = backend


def dsn() -> str:
    return os.environ.get("WAREHOUSE_DSN") or "postgresql://inspector_ro:inspector_ro@localhost:5439/analytics"


def timeout_s() -> int:
    return int(os.environ.get("WAREHOUSE_TIMEOUT_S") or "60")


def connect():
    import psycopg

    conn = psycopg.connect(dsn(), connect_timeout=10, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {timeout_s() * 1000}")
        cur.execute("SET default_transaction_read_only = on")
    return conn


def json_value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return v


def _postgres(sql: str) -> tuple[list[str], list[list]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [d.name for d in cur.description or []]
        return columns, [[json_value(v) for v in row] for row in cur.fetchall()]


register_backend("postgres", _postgres)


def fetch(sql: str) -> tuple[list[str], list[list]]:
    name = os.environ.get("WAREHOUSE_BACKEND") or "postgres"
    if name not in _BACKENDS:
        raise ValueError(f"unknown WAREHOUSE_BACKEND {name!r} (register_backend)")
    return _BACKENDS[name](sql)


def identifier(name: str) -> str:
    """Validate a (schema-)qualified identifier built into internal SQL."""
    parts = name.lower().split(".")
    if not 1 <= len(parts) <= 2 or not all(_IDENT.match(p) for p in parts):
        raise ValueError(f"invalid identifier {name!r}")
    return ".".join(parts)
