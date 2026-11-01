"""Package inputs and run outputs are plug-in by location scheme: local (as today), S3 (tested against moto's fake
S3), and anything a host registers."""

import json
from pathlib import Path

import pytest

from conftest import PACKAGES, needs_synthetic, pkg

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with moto.mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="migrations")
        yield client


def _upload(client, folder: Path, prefix: str):
    for f in folder.rglob("*"):
        if f.is_file():
            client.upload_file(str(f), "migrations", f"{prefix}/{f.relative_to(folder)}")


@needs_synthetic
def test_package_from_s3_validates_like_the_local_one(s3):
    from inspector_agent.inspector_agent import run_rules_only

    _upload(s3, PACKAGES / "S07", "incoming/S07")
    remote = run_rules_only("s3://migrations/incoming/S07/")
    local = run_rules_only(pkg("S07"))
    assert remote.verdict == local.verdict == "NOT_READY"
    assert [(f.check, f.status) for f in remote.findings] == [(f.check, f.status) for f in local.findings]


@needs_synthetic
def test_zipped_package_in_s3(s3, tmp_path):
    import shutil

    from inspector_agent.inspector_agent import run_rules_only

    archive = shutil.make_archive(str(tmp_path / "S07"), "zip", PACKAGES, "S07")  # one top folder, as people zip it
    s3.upload_file(archive, "migrations", "incoming/S07.zip")
    report = run_rules_only("s3://migrations/incoming/S07.zip")
    assert report.verdict == "NOT_READY" and not [f for f in report.findings if f.check == "PK-01"]


@needs_synthetic
def test_run_folder_goes_to_s3(s3, monkeypatch):
    from inspector_agent import runs
    from inspector_agent.inspector_agent import run_rules_only

    monkeypatch.setenv("INSPECTOR_RUNS_DIR", "s3://migrations/reports")
    report = run_rules_only(pkg("S01"))
    where = runs.save(report, {"mode": "rules"})
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket="migrations", Prefix="reports/")["Contents"]}
    assert where.startswith("s3://migrations/reports/") and {k.rsplit("/", 1)[1] for k in keys} == {
        "report.md", "report.json", "run.json"}
    body = s3.get_object(Bucket="migrations", Key=next(k for k in keys if k.endswith("report.json")))["Body"].read()
    assert json.loads(body)["verdict"] == report.verdict


def test_hosts_can_plug_in_their_own_storage(tmp_path, monkeypatch):
    from inspector_agent import runs
    from inspector_agent.tools.package_inventory import locations
    from inspector_agent.tools.findings_ledger import Report

    (tmp_path / "pkg").mkdir()
    locations.register_reader("artifacts", lambda loc: (tmp_path / "pkg", "fp-1"))
    assert locations.materialize("artifacts://build-42") == (tmp_path / "pkg", "fp-1")
    with pytest.raises(ValueError, match="no reader for gs://"):
        locations.materialize("gs://bucket/x")

    written = {}
    runs.register_writer("mem", lambda dest, files: written.setdefault(dest, sorted(files)) and dest)
    monkeypatch.setenv("INSPECTOR_RUNS_DIR", "mem://runs")
    where = runs.save(Report(run_id="R-1", package_id="P", verdict="READY"), {})
    assert where.startswith("mem://runs/") and written[where] == ["report.json", "report.md", "run.json"]
