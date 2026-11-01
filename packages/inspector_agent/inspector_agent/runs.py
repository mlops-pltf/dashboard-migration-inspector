"""Every validation run leaves a folder developers can open without logging in anywhere:

  <INSPECTOR_RUNS_DIR>/<yyyymmdd-hhmmss>-<package>-<run id>/
    report.md     the report
    report.json   the same, structured
    run.json      mode, timings, the agent's model, tool calls and token use

INSPECTOR_RUNS_DIR is a destination whose scheme picks the writer: a local folder (or file://...), s3://bucket/prefix
(AWS credentials from the environment or the runtime role), or anything a host adds with register_writer().
Unset: nothing is written (the report still comes back in the response).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from .tools.findings_ledger import Report, markdown

Writer = Callable[[str, dict[str, bytes]], str]  # (destination folder, {file name: content}) -> where it went
_WRITERS: dict[str, Writer] = {}


def register_writer(scheme: str, writer: Writer) -> None:
    """Add a destination for run folders, e.g. a host's artifact store: INSPECTOR_RUNS_DIR=<scheme>://..."""
    _WRITERS[scheme.lower()] = writer


def _local(destination: str, files: dict[str, bytes]) -> str:
    folder = Path(destination.removeprefix("file://"))
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)
    return str(folder)


def _s3(destination: str, files: dict[str, bytes]) -> str:
    import boto3

    bucket, _, prefix = destination.removeprefix("s3://").partition("/")
    s3 = boto3.client("s3")
    for name, content in files.items():
        kind = {"md": "text/markdown", "json": "application/json"}.get(name.rpartition(".")[2], "application/octet-stream")
        s3.put_object(Bucket=bucket, Key=f"{prefix.strip('/')}/{name}".lstrip("/"), Body=content, ContentType=kind)
    return destination


register_writer("", _local)
register_writer("file", _local)
register_writer("s3", _s3)


def save(report: Report, meta: dict) -> str | None:
    root = os.environ.get("INSPECTOR_RUNS_DIR")
    if not root:
        return None
    scheme = root.split("://", 1)[0].lower() if "://" in root else ""
    if scheme not in _WRITERS:
        raise ValueError(f"no writer for {scheme}:// destinations (register_writer)")
    stamp = "".join(ch for ch in report.created_at[:19] if ch.isdigit())  # 2026-10-25T07:38:42 -> 20261025073842
    folder = f"{root.rstrip('/')}/{stamp[:8]}-{stamp[8:]}-{report.package_id}-{report.run_id}"
    return _WRITERS[scheme](folder, {
        "report.md": markdown(report).encode(),
        "report.json": report.model_dump_json(indent=2).encode(),
        "run.json": json.dumps(meta, indent=2, default=str).encode(),
    })
