"""Packages and runs, in SQLite under PORTAL_DATA_DIR. Run results (the Inspector's full answer) are JSON files."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL, location TEXT NOT NULL, check_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES packages(id), mode TEXT NOT NULL,
    instructions TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL,
    verdict TEXT, counts_json TEXT, error TEXT, trace_url TEXT, report_run_id TEXT, context_id TEXT);
"""


def data_dir() -> Path:
    path = Path(os.environ.get("PORTAL_DATA_DIR", "./portal-data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir() -> Path:
    """Where packages are stored; the agents read them from here (PORTAL_UPLOADS_DIR, also on their PACKAGE_ROOT)."""
    path = Path(os.environ.get("PORTAL_UPLOADS_DIR") or data_dir() / "uploads").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def results_dir() -> Path:
    path = data_dir() / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(data_dir() / "portal.db", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("check_json", "counts_json"):
        if key in out:
            out[key.removesuffix("_json")] = json.loads(out.pop(key) or "null")
    return out


# -- packages ---------------------------------------------------------------------------------
def add_package(pid: str, name: str, location: str, check: dict) -> dict:
    with _lock, _db() as con:
        con.execute("INSERT INTO packages VALUES (?,?,?,?,?)", (pid, name, time.time(), location, json.dumps(check)))
    return get_package(pid)


def get_package(pid: str) -> dict | None:
    with _db() as con:
        return _row(con.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone())


def list_packages() -> list[dict]:
    with _db() as con:
        return [_row(r) for r in con.execute("SELECT * FROM packages ORDER BY created DESC")]


# -- runs -------------------------------------------------------------------------------------
def add_run(rid: str, package_id: str, mode: str, instructions: str) -> dict:
    with _lock, _db() as con:
        con.execute("INSERT INTO runs (id, package_id, mode, instructions, status, created) VALUES (?,?,?,?,?,?)",
                    (rid, package_id, mode, instructions, "queued", time.time()))
    return get_run(rid)


def update_run(rid: str, **fields: Any) -> None:
    if "counts" in fields:
        fields["counts_json"] = json.dumps(fields.pop("counts"))
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, _db() as con:
        con.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), rid))


def get_run(rid: str) -> dict | None:
    with _db() as con:
        return _row(con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())


def list_runs(package_id: str | None = None) -> list[dict]:
    with _db() as con:
        if package_id:
            rows = con.execute("SELECT * FROM runs WHERE package_id=? ORDER BY created DESC", (package_id,))
        else:
            rows = con.execute("SELECT * FROM runs ORDER BY created DESC")
        return [_row(r) for r in rows]


def previous_done_run(run: dict) -> dict | None:
    """The last finished run of the same package before this one (to compare with)."""
    with _db() as con:
        return _row(con.execute(
            "SELECT * FROM runs WHERE package_id=? AND status='done' AND created<? ORDER BY created DESC LIMIT 1",
            (run["package_id"], run["created"])).fetchone())


def interrupted_runs() -> list[str]:
    """Runs left queued/running by a restart."""
    with _db() as con:
        return [r["id"] for r in con.execute("SELECT id FROM runs WHERE status IN ('queued','running')")]


# -- results ----------------------------------------------------------------------------------
def save_result(rid: str, result: dict) -> None:
    (results_dir() / f"{rid}.json").write_text(json.dumps(result))


def load_result(rid: str) -> dict | None:
    path = results_dir() / f"{rid}.json"
    return json.loads(path.read_text()) if path.exists() else None
