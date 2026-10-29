"""Rule-sweep quality: the deterministic leads reproduce the ground truth for every synthetic scenario.

* recall: every expected finding (in scope for static checks) is reported with the expected status
* precision: no blocking finding that the ground truth does not expect
* must_pass: fields that must not be flagged as the primary subject of a blocking finding
* verdict: the static verdict matches, where all expected blocking findings are in scope
"""

from pathlib import Path

import pytest
import yaml

from inspector_agent.inspector_agent import run_rules_only

ROOT = Path(__file__).resolve().parents[3] / "synthetic"
IDS = sorted(p.name for p in (ROOT / "packages").iterdir()) if (ROOT / "packages").exists() else []

# checks implemented by static validation
STATIC_CHECKS = {"CV-01", "CV-02", "LN-01", "AG-01", "AG-02", "AG-03", "FL-01", "FL-02",
                 "FX-01", "FX-02", "FX-03", "JN-01", "JN-02", "JN-04", "JN-05", "JN-06", "JN-07",
                 "DB-01", "DB-02", "DB-05", "DB-06", "LF-01", "LF-02"}

pytestmark = pytest.mark.skipif(not IDS, reason="run `uv run inspector-synth generate` first")


@pytest.fixture(scope="module", params=IDS)
def case(request):
    sid = request.param
    truth = yaml.safe_load((ROOT / "ground_truth" / sid / "expected_findings.yaml").read_text())
    return sid, truth, run_rules_only(str(ROOT / "packages" / sid))


def _accepts(expected: dict) -> set[str]:
    return {expected["check"], *expected.get("also_acceptable", [])}


def test_recall(case):
    sid, truth, report = case
    for e in truth["findings"]:
        if not _accepts(e) & STATIC_CHECKS:
            continue
        hits = [f for f in report.findings if f.check in _accepts(e) and f.status == e["status"]
                and f.severity == e["severity"]]
        assert hits, f"{sid} {e['id']}: expected {e['check']} {e['status']} ({e['note']})"


def test_precision(case):
    sid, truth, report = case
    for f in report.findings:
        if not f.blocking:
            continue
        ok = any(f.check in _accepts(e) and f.status == e["status"] for e in truth["findings"])
        assert ok, f"{sid}: unexpected {f.check} {f.status}: {f.title}"


def test_must_pass(case):
    sid, truth, report = case
    flagged = {f.thoughtspot.name for f in report.findings if f.blocking and f.thoughtspot}
    for ref in truth.get("must_pass", []):
        assert ref.split("::")[-1] not in flagged, f"{sid}: {ref} must not be flagged"


def test_verdict(case):
    sid, truth, report = case
    out_of_scope = [e for e in truth["findings"] if e["severity"] == "BLOCKER" and not _accepts(e) & STATIC_CHECKS]
    if out_of_scope:
        pytest.skip(f"{sid}: verdict depends on checks not implemented statically")
    assert report.verdict == truth["expected_verdict"]


def test_report_json_round_trips(case):
    _, _, report = case
    from inspector_agent.tools.findings_ledger import Report

    assert Report.model_validate_json(report.model_dump_json()) == report
