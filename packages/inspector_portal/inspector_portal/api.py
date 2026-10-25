"""Portal backend (FastAPI). The lasting part of the portal: any UI (Streamlit now, React later) uses this API.

  POST /api/packages                 upload a package (multipart), checked by the experts
  GET  /api/packages[/{id}]
  POST /api/runs                     {package_id, mode, instructions} -> queued run
  GET  /api/runs[?package_id=]       runs, newest first
  GET  /api/runs/{id}                status; when done: verdict, report, comparison with the previous run
  GET  /api/runs/{id}/report.md|json downloads
  POST /api/runs/{id}/questions      {question} -> the Inspector's answer, in the run's conversation
  GET  /api/runs/{id}/questions

Environment: PORTAL_DATA_DIR, PORTAL_UPLOADS_DIR (where packages are written), PORTAL_AGENT_UPLOADS_PATH (the
same folder as the agents see it), PORTAL_*_URL (see agents.py), PORTAL_MAX_PARALLEL_RUNS.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import jobs, store
from .agents import check_package, client
from .compare import compare
from .uploads import Upload, UploadError, build_package, slug

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    jobs.recover()  # runs a restart interrupted are marked failed
    yield


app = FastAPI(title="Inspector portal", version="0.1.0", lifespan=_lifespan)


def _agent_location(package_id: str) -> str:
    base = os.environ.get("PORTAL_AGENT_UPLOADS_PATH") or str(store.uploads_dir())
    return f"{base.rstrip('/')}/{package_id}"


async def _read(files: list[UploadFile] | None) -> list[Upload]:
    return [Upload(f.filename or "upload", await f.read()) for f in files or [] if f.filename]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# -- packages ---------------------------------------------------------------------------------
@app.post("/api/packages")
async def upload_package(name: str = Form(...), thoughtspot: list[UploadFile] | None = File(None),
                         strategy: list[UploadFile] | None = File(None), mapping: UploadFile | None = File(None),
                         package_zip: UploadFile | None = File(None)) -> dict:
    pid = f"{slug(name)}-{uuid.uuid4().hex[:6]}"
    whole = (await _read([package_zip]) or [None])[0] if package_zip else None
    maps = await _read([mapping]) if mapping else []
    try:
        build_package(store.uploads_dir(), pid, name, await _read(thoughtspot), await _read(strategy),
                      mapping=maps[0] if maps else None, whole=whole)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    location = _agent_location(pid)
    return store.add_package(pid, name, location, check_package(location))


@app.get("/api/packages")
def packages() -> list[dict]:
    return store.list_packages()


@app.get("/api/packages/{pid}")
def package(pid: str) -> dict:
    return store.get_package(pid) or _missing("package")


# -- runs -------------------------------------------------------------------------------------
class RunRequest(BaseModel):
    package_id: str
    mode: Literal["agent", "rules+data", "rules"] = "agent"
    instructions: str = Field("", max_length=4000)


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    if not store.get_package(req.package_id):
        _missing("package")
    run = store.add_run(f"run-{uuid.uuid4().hex[:10]}", req.package_id, req.mode, req.instructions.strip())
    jobs.submit(run["id"])
    return run


@app.get("/api/runs")
def runs(package_id: str | None = None) -> list[dict]:
    return [_with_elapsed(r) for r in store.list_runs(package_id)]


@app.get("/api/runs/{rid}")
def run(rid: str) -> dict:
    r = store.get_run(rid) or _missing("run")
    out = _with_elapsed(r)
    result = store.load_result(rid)
    if result:
        out["summary"] = result.get("summary")
        out["report"] = result.get("report")
        out["run_folder"] = result.get("run_folder")
        prev = store.previous_done_run(r)
        prev_result = store.load_result(prev["id"]) if prev else None
        if prev_result and prev_result.get("report"):
            out["comparison"] = compare(result["report"], prev_result["report"]) | {"previous_run": prev["id"]}
    return out


@app.get("/api/runs/{rid}/report.md", response_class=PlainTextResponse)
def report_md(rid: str) -> str:
    return (store.load_result(rid) or _missing("report"))["markdown"]


@app.get("/api/runs/{rid}/report.json")
def report_json(rid: str) -> dict:
    return (store.load_result(rid) or _missing("report"))["report"]


# -- follow-up questions ----------------------------------------------------------------------
class Question(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


def _chat_path(rid: str):
    return store.results_dir() / f"{rid}.chat.json"


@app.get("/api/runs/{rid}/questions")
def questions(rid: str) -> list[dict]:
    path = _chat_path(rid)
    return json.loads(path.read_text()) if path.exists() else []


@app.post("/api/runs/{rid}/questions")
def ask(rid: str, q: Question) -> dict:
    r = store.get_run(rid) or _missing("run")
    result = store.load_result(rid) or _missing("finished run")
    package = store.get_package(r["package_id"])
    text = q.question.strip()
    if not r.get("context_id"):  # the first question carries the run as context
        report = result.get("report") or {}
        findings = "\n".join(f"- {f.get('id')} {f.get('check')} {f.get('status')} {f.get('severity')}: {f.get('title')}"
                             for f in report.get("findings", []) if f.get("status") != "PASS")
        text = (f"A developer asks about validation run {result.get('run_id')} of the package at "
                f"{package['location']} (use this location as the `package` argument of your tools). "
                f"Verdict: {result.get('verdict')}.\nOpen findings:\n{findings or '- none'}\n\n"
                f"Summary given to the developer:\n{result.get('summary') or '-'}\n\nQuestion: {text}")
    answer, context_id = client("inspector").ask(text, context_id=r.get("context_id"))
    if context_id and not r.get("context_id"):
        store.update_run(rid, context_id=context_id)
    chat = questions(rid) + [{"question": q.question.strip(), "answer": answer, "at": time.time()}]
    _chat_path(rid).write_text(json.dumps(chat))
    return chat[-1]


# -- helpers ----------------------------------------------------------------------------------
def _with_elapsed(r: dict) -> dict:
    end = r.get("finished") or (time.time() if r.get("started") else None)
    return r | {"elapsed_seconds": round(end - r["started"]) if end and r.get("started") else None}


def _missing(what: str):
    raise HTTPException(status_code=404, detail=f"{what} not found")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
