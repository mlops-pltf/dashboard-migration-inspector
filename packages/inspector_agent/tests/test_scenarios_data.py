"""Rules + data: the leads' data probes run through the data agent (in-process, real warehouse) and the result
reproduces the ground truth after data: statuses, latent defects and verdicts, for every synthetic scenario."""

import pytest
import yaml

from inspector_agent.inspector_agent import run_rules_only

from conftest import GROUND_TRUTH, IDS, needs_synthetic, needs_warehouse, pkg
from test_scenarios import STATIC_CHECKS, _accepts

pytestmark = [needs_synthetic, needs_warehouse]


@pytest.fixture(scope="module", params=IDS)
def case(request):
    sid = request.param
    truth = yaml.safe_load((GROUND_TRUTH / sid / "expected_findings.yaml").read_text())
    return sid, truth, run_rules_only(pkg(sid), with_data=True)


def _after(e: dict) -> str:
    return e.get("status_after_data") or e["status"]


def test_statuses_after_data(case):
    sid, truth, report = case
    for e in truth["findings"]:
        if not _accepts(e) & STATIC_CHECKS:
            continue
        hits = [f for f in report.findings if f.check in _accepts(e) and f.status == _after(e)
                and f.severity == e["severity"]]
        assert hits, f"{sid} {e['id']}: expected {e['check']} {_after(e)} after data ({e['note']})"
        if e.get("latent"):
            assert any(f.latent for f in hits), f"{sid} {e['id']}: expected a latent defect"


def test_no_unexpected_blockers_or_latent(case):
    sid, truth, report = case
    for f in report.findings:
        if f.blocking:
            ok = any(f.check in _accepts(e) and f.status == _after(e) for e in truth["findings"])
            assert ok, f"{sid}: unexpected {f.check} {f.status} after data: {f.title}"
        if f.latent:
            assert any(f.check in _accepts(e) and e.get("latent") for e in truth["findings"]), \
                f"{sid}: unexpected latent {f.check}: {f.title}"


def test_must_pass_after_data(case):
    sid, truth, report = case
    flagged = {f.thoughtspot.name for f in report.findings if f.blocking and f.thoughtspot}
    for ref in truth.get("must_pass", []):
        assert ref.split("::")[-1] not in flagged, f"{sid}: {ref} must not be flagged"


def test_verdict_after_data(case):
    sid, truth, report = case
    if any(e["severity"] == "BLOCKER" and not _accepts(e) & STATIC_CHECKS for e in truth["findings"]):
        pytest.skip(f"{sid}: verdict depends on content checks (P6)")
    assert report.stage == "rules+data"
    assert report.verdict == truth.get("expected_verdict_after_data", truth["expected_verdict"])
