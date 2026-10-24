"""Read-only warehouse access.

  WAREHOUSE_DSN              postgresql://user:pass@host:port/db  (Redshift speaks the same protocol)
  WAREHOUSE_DIALECT          postgres | redshift   (SQL arrives in Redshift dialect and is transpiled)
  WAREHOUSE_TIMEOUT_S        statement timeout (default 60)
  WAREHOUSE_ALLOWED_SCHEMAS  comma-separated (default retail)
Every session is read-only with a statement timeout, on top of the database role's own restrictions.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from decimal import Decimal

import psycopg

_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")


def dsn() -> str:
    return os.environ.get("WAREHOUSE_DSN", "postgresql://inspector_ro:inspector_ro@localhost:5439/analytics")


def timeout_s() -> int:
    return int(os.environ.get("WAREHOUSE_TIMEOUT_S", "60"))


def connect() -> psycopg.Connection:
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


def fetch(sql: str, params: tuple | None = None) -> tuple[list[str], list[list]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description or []]
        return columns, [[json_value(v) for v in row] for row in cur.fetchall()]


def identifier(name: str) -> str:
    """Validate a (schema-)qualified identifier built into internal SQL."""
    parts = name.lower().split(".")
    if not 1 <= len(parts) <= 2 or not all(_IDENT.match(p) for p in parts):
        raise ValueError(f"invalid identifier {name!r}")
    return ".".join(parts)
