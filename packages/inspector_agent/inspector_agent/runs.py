"""Every validation run leaves a folder developers can open without logging in anywhere:

  <INSPECTOR_RUNS_DIR>/<yyyymmdd-hhmmss>-<package>-<run id>/
    report.md     the report
    report.json   the same, structured
    run.json      mode, timings, the agent's model, tool calls and token use

Unset INSPECTOR_RUNS_DIR: nothing is written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .tools.findings_ledger import Report, markdown


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
