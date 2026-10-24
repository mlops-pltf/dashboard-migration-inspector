"""Live evaluation of the agent against the synthetic ground truth (needs a model: INSPECTOR_LLM_MODEL_*).

    INSPECTOR_LIVE_EVAL=1 [INSPECTOR_EVAL_SCENARIOS=S01,S04,S13] uv run pytest packages/inspector_agent/tests/test_agent_eval.py -s

Scores per scenario: recall (expected findings found), precision (no unexpected blocking findings),
must-pass fields not flagged, verdict. Content checks (DB-*, LF-*) arrive with P6.
"""

import os

import pytest
import yaml

from conftest import GROUND_TRUTH, IDS, pkg

LIVE = os.environ.get("INSPECTOR_LIVE_EVAL") == "1"
SELECTED = [s for s in os.environ.get("INSPECTOR_EVAL_SCENARIOS", ",".join(IDS)).split(",") if s in IDS]
IN_SCOPE = {"CV-01", "CV-02", "LN-01", "AG-01", "AG-02", "AG-03", "FL-01", "FL-02", "FL-03", "FX-01", "FX-02",
            "FX-03", "FX-04", "JN-01", "JN-02", "JN-03", "JN-04", "JN-05", "JN-06", "JN-07"}

pytestmark = pytest.mark.skipif(not LIVE, reason="set INSPECTOR_LIVE_EVAL=1 (needs a model: INSPECTOR_LLM_MODEL_*)")


def accepts(e: dict) -> set[str]:
    return {e["check"], *e.get("also_acceptable", [])}


@pytest.fixture(scope="module", params=SELECTED)
def run(request):
    from inspector_agent.inspector_agent import run_validation

    sid = request.param
    truth = yaml.safe_load((GROUND_TRUTH / sid / "expected_findings.yaml").read_text())
    report = run_validation(pkg(sid))
    print(f"\n{sid}: {report.verdict} (expected {truth['expected_verdict']}) · {report.counts} · {report.agent}")
    return sid, truth, report


def test_recall(run):
    sid, truth, report = run
    for e in truth["findings"]:
        if not accepts(e) & IN_SCOPE:
            continue
        assert any(f.check in accepts(e) and f.status == e["status"] for f in report.findings), \
            f"{sid} missed {e['check']} {e['status']}: {e['note']}"


def test_precision(run):
    sid, truth, report = run
    for f in report.findings:
        if f.blocking:
            assert any(f.check in accepts(e) and f.status == e["status"] for e in truth["findings"]), \
                f"{sid} unexpected {f.check} {f.status}: {f.title}"


def test_must_pass(run):
    sid, truth, report = run
    flagged = {f.thoughtspot.name for f in report.findings if f.blocking and f.thoughtspot}
    for ref in truth.get("must_pass", []):
        assert ref.split("::")[-1] not in flagged, f"{sid}: {ref} must not be flagged"


def test_agent_finished_and_resolved_every_lead(run):
    sid, _, report = run
    assert report.agent.get("unreviewed_leads", 0) == 0, f"{sid}: agent left leads unreviewed"
    assert "stopped" not in report.agent, report.agent.get("stopped")
    assert report.summary
