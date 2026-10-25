import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inspector_portal import api as api_module
from inspector_portal import jobs, store
from inspector_portal.compare import compare
from inspector_portal.uploads import Upload, UploadError, build_package

S04 = Path(__file__).resolve().parents[3] / "synthetic" / "packages" / "S04"
needs_synthetic = pytest.mark.skipif(not S04.exists(), reason="run `uv run inspector-synth generate` first")


def zip_of(folder: Path, prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in folder.rglob("*"):
            if p.is_file():
                zf.write(p, f"{prefix}{p.relative_to(folder)}")
    return buf.getvalue()


def raw_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PORTAL_UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("PORTAL_AGENT_UPLOADS_PATH", raising=False)
    return tmp_path


# -- uploads ----------------------------------------------------------------------------------
@needs_synthetic
def test_two_exports_become_a_package(tmp_path):
    folder = build_package(tmp_path, "p1", "Sales Orders",
                           thoughtspot=[Upload("ts.zip", zip_of(S04 / "thoughtspot", "Sales Orders/"))],
                           strategy=[Upload("st.zip", zip_of(S04 / "strategy"))])
    assert (folder / "thoughtspot" / "Sales Orders.worksheet.tml").exists()  # the zip's single folder is stripped
    assert (folder / "strategy" / "model.yaml").exists() and (folder / "strategy" / "dashboards").is_dir()
    assert "package_id: p1" in (folder / "manifest.yaml").read_text()


@needs_synthetic
def test_loose_strategy_files_are_placed_by_content(tmp_path):
    files = [Upload(p.name, p.read_bytes()) for p in (S04 / "strategy").rglob("*") if p.is_file()]
    tml = [Upload(p.name, p.read_bytes()) for p in (S04 / "thoughtspot").glob("*.tml")]
    folder = build_package(tmp_path, "p2", "x", thoughtspot=tml, strategy=files)
    assert (folder / "strategy" / "dashboards" / "Sales Overview.json").exists()
    assert (folder / "strategy" / "sql" / "model_sqlview.json").exists()


@needs_synthetic
def test_whole_package_zip(tmp_path):
    folder = build_package(tmp_path, "p3", "x", [], [], whole=Upload("pkg.zip", zip_of(S04, "S04/")))
    assert (folder / "thoughtspot").is_dir() and (folder / "strategy" / "model.yaml").exists()


@pytest.mark.parametrize("name", ["../evil.tml", "/etc/evil.tml", "a/../../evil.tml"])
def test_unsafe_zip_paths_are_refused(tmp_path, name):
    with pytest.raises(UploadError, match="unsafe"):
        build_package(tmp_path, "bad", "x", [Upload("ts.zip", raw_zip({name: b"x"}))],
                      [Upload("m.yaml", b"a: 1")])
    assert not (tmp_path / "bad").exists() and not (tmp_path.parent / "evil.tml").exists()


def test_missing_side_and_wrong_types_are_refused(tmp_path):
    with pytest.raises(UploadError, match="both sides"):
        build_package(tmp_path, "a", "x", [Upload("w.tml", b"worksheet: {}")], [])
    with pytest.raises(UploadError, match="expected a zip"):
        build_package(tmp_path, "b", "x", [Upload("w.exe", b"MZ")], [Upload("m.yaml", b"a: 1")])
    with pytest.raises(UploadError, match="mapping"):
        build_package(tmp_path, "c", "x", [Upload("w.tml", b"worksheet: {}")], [Upload("m.yaml", b"a: 1")],
                      mapping=Upload("mapping.yaml", b"a: [unclosed"))


# -- comparison -------------------------------------------------------------------------------
def test_compare_runs():
    f = lambda check, name, status: {"check": check, "status": status, "severity": "BLOCKER", "title": check,  # noqa: E731
                                     "thoughtspot": {"name": name}, "strategy": {"name": name}}
    before = {"verdict": "NOT_READY", "findings": [f("JN-04", "Revenue", "FAIL"), f("FL-01", "Filter", "FAIL")]}
    now = {"verdict": "NOT_READY", "findings": [f("FL-01", "Filter", "FAIL"), f("AG-02", "Region", "UNVERIFIED"),
                                                f("JN-04", "Revenue", "PASS")]}
    out = compare(now, before)
    assert [x["check"] for x in out["fixed"]] == ["JN-04"]
    assert [x["check"] for x in out["still_open"]] == ["FL-01"]
    assert [x["check"] for x in out["new"]] == ["AG-02"]


# -- API, with a fake Inspector and experts ------------------------------------------------------
class FakeInspector:
    def __init__(self):
        self.calls = []

    def call(self, skill, args):
        self.calls.append(args)
        verdict = "NOT_READY" if len(self.calls) == 1 else "READY"
        findings = [{"check": "JN-04", "status": "FAIL", "severity": "BLOCKER", "title": "fan-out",
                     "thoughtspot": {"name": "Revenue"}, "strategy": {"name": "SQL"}}] if verdict == "NOT_READY" else []
        return {"run_id": f"R-{len(self.calls)}", "verdict": verdict, "counts": {"blocker_fail": len(findings)},
                "summary": "s", "markdown": "# report", "trace_url": None,
                "report": {"verdict": verdict, "findings": findings, "developer_notes": args["instructions"]}}

    def ask(self, question, context_id=None):
        return f"answer to: {question[-20:]}", context_id or "ctx-1"


@pytest.fixture
def client(monkeypatch):
    fake = FakeInspector()
    monkeypatch.setattr(api_module, "check_package", lambda loc: {"ok": True, "sides": {}})
    monkeypatch.setattr(api_module, "client", lambda name: fake)
    monkeypatch.setattr(jobs, "client", lambda name: fake)
    monkeypatch.setattr(jobs, "submit", jobs._run)  # run synchronously
    with TestClient(api_module.app) as c:
        yield c, fake


@needs_synthetic
def test_upload_run_compare_ask(client):
    c, fake = client
    files = [("thoughtspot", ("ts.zip", zip_of(S04 / "thoughtspot"))), ("strategy", ("st.zip", zip_of(S04 / "strategy")))]
    pkg = c.post("/api/packages", data={"name": "Sales Orders"}, files=files).json()
    assert pkg["id"].startswith("sales-orders-") and pkg["check"]["ok"]

    first = c.post("/api/runs", json={"package_id": pkg["id"], "mode": "agent",
                                      "instructions": "Region filter removed on purpose"}).json()
    r1 = c.get(f"/api/runs/{first['id']}").json()
    assert r1["status"] == "done" and r1["verdict"] == "NOT_READY" and "comparison" not in r1
    assert fake.calls[0]["instructions"] == "Region filter removed on purpose"
    assert fake.calls[0]["package"].endswith(pkg["id"])
    assert c.get(f"/api/runs/{first['id']}/report.md").text == "# report"

    time.sleep(0.01)
    second = c.post("/api/runs", json={"package_id": pkg["id"], "mode": "agent"}).json()
    r2 = c.get(f"/api/runs/{second['id']}").json()
    assert r2["verdict"] == "READY" and [f["check"] for f in r2["comparison"]["fixed"]] == ["JN-04"]

    a1 = c.post(f"/api/runs/{second['id']}/questions", json={"question": "why?"}).json()
    c.post(f"/api/runs/{second['id']}/questions", json={"question": "and then?"})
    chat = c.get(f"/api/runs/{second['id']}/questions").json()
    assert len(chat) == 2 and a1["answer"].startswith("answer to")
    assert store.get_run(second["id"])["context_id"] == "ctx-1"


def test_upload_errors_are_400(client):
    c, _ = client
    r = c.post("/api/packages", data={"name": "x"}, files=[("thoughtspot", ("a.tml", b"worksheet: {}"))])
    assert r.status_code == 400 and "both sides" in r.json()["detail"]


def test_inspector_down_fails_the_run(client, monkeypatch, tmp_path):
    c, _ = client
    pid = "p-down"
    (Path(store.uploads_dir()) / pid).mkdir()
    store.add_package(pid, "x", str(Path(store.uploads_dir()) / pid), {"ok": True})

    class Down:
        def call(self, *a):
            raise ConnectionError("refused")

    monkeypatch.setattr(jobs, "client", lambda name: Down())
    run = c.post("/api/runs", json={"package_id": pid}).json()
    r = c.get(f"/api/runs/{run['id']}").json()
    assert r["status"] == "failed" and "refused" in r["error"] and r.get("verdict") is None
