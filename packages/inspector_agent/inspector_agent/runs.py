"""Every validation run leaves a folder developers can open without logging in anywhere:

  <INSPECTOR_RUNS_DIR>/<yyyymmdd-hhmmss>-<package>-<run id>/
    report.md     the report
    report.json   the same, structured
    run.json      mode, timings, trace id and the Langfuse link to see how the run got there

Unset INSPECTOR_RUNS_DIR: nothing is written.
"""

from __future__ import annotations

import functools
import json
import os
import urllib.request
from pathlib import Path

from inspector_contract import telemetry

from .tools.findings_ledger import Report, markdown


@functools.lru_cache(maxsize=1)
def _langfuse_project() -> str | None:
    auth = telemetry.langfuse_auth()
    if not auth:
        return None
    req = urllib.request.Request(f"{telemetry.langfuse_base_url()}/api/public/projects",
                                 headers={"Authorization": auth, "User-Agent": "inspector-agent"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)["data"][0]["id"]
    except Exception:  # the link is a convenience; the run never fails for it
        return None


def trace_url(trace_id: str) -> str | None:
    project = _langfuse_project()
    return f"{telemetry.langfuse_base_url()}/project/{project}/traces/{trace_id}" if project else None


def save(report: Report, meta: dict) -> Path | None:
    root = os.environ.get("INSPECTOR_RUNS_DIR")
    if not root:
        return None
    stamp = "".join(ch for ch in report.created_at[:19] if ch.isdigit())  # 2026-10-25T07:38:42 -> 20261025073842
    folder = Path(root) / f"{stamp[:8]}-{stamp[8:]}-{report.package_id}-{report.run_id}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(markdown(report))
    (folder / "report.json").write_text(report.model_dump_json(indent=2))
    (folder / "run.json").write_text(json.dumps(meta, indent=2, default=str))
    return folder
