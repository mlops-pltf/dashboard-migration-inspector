"""Validations run in the background: a few at a time, each one Inspector `validate` call over A2A."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import store
from .agents import client

_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("PORTAL_MAX_PARALLEL_RUNS", "2")),
                           thread_name_prefix="validation")


def submit(run_id: str) -> None:
    _pool.submit(_run, run_id)


def _run(run_id: str) -> None:
    run = store.get_run(run_id)
    package = store.get_package(run["package_id"])
    store.update_run(run_id, status="running", started=time.time())
    try:
        result = client("inspector").call("validate", {
            "package": package["location"], "mode": run["mode"], "instructions": run["instructions"],
            "max_turns": int(os.environ.get("PORTAL_MAX_TURNS", "60"))})
    except Exception as exc:  # the Inspector is down or the call failed: the run fails, nothing is invented
        store.update_run(run_id, status="failed", finished=time.time(), error=f"{type(exc).__name__}: {exc}")
        return
    if "error" in result and "verdict" not in result:
        store.update_run(run_id, status="failed", finished=time.time(), error=str(result["error"]))
        return
    store.save_result(run_id, result)
    store.update_run(run_id, status="done", finished=time.time(), verdict=result.get("verdict"),
                     counts=result.get("counts"), trace_url=result.get("trace_url"),
                     report_run_id=result.get("run_id"))


def recover() -> None:
    """Runs a restart interrupted are marked failed: their Inspector call is gone."""
    for rid in store.interrupted_runs():
        store.update_run(rid, status="failed", finished=time.time(), error="interrupted by a portal restart")
