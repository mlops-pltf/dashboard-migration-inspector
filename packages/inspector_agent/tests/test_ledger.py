"""The evidence rules the agent cannot bypass."""

from conftest import needs_synthetic, pkg
from inspector_agent.tools.definition_comparer.tool import compare_fields_impl
from inspector_agent.tools.findings_ledger import Ledger
from inspector_agent.tools.rule_sweep.tool import run_rule_sweep_impl

pytestmark = needs_synthetic


def ledger_with_sweep(sid: str) -> tuple[Ledger, str, dict]:
    ledger = Ledger(package=pkg(sid))
    out = run_rule_sweep_impl(pkg(sid))
    eid = ledger.add_evidence("run_rule_sweep", {"package": pkg(sid)}, out)
    return ledger, eid, out


def test_evidence_is_required():
    ledger, _, _ = ledger_with_sweep("S07")
    r = ledger.record(check="AG-01", severity="BLOCKER", status="FAIL", title="t", explanation="e", evidence_ids=[])
    assert not r["accepted"]
    r = ledger.record(check="AG-01", severity="BLOCKER", status="FAIL", title="t", explanation="e", evidence_ids=["E99"])
    assert not r["accepted"]


def test_blocker_pass_needs_proof():
    ledger, sweep_id, _ = ledger_with_sweep("S14")
    r = ledger.record(check="FX-01", severity="BLOCKER", status="PASS", title="looks the same", explanation="I think so",
                      evidence_ids=[sweep_id], thoughtspot_object="Sales Orders::Net Revenue",
                      strategy_object="Sales Orders::Net Revenue")
    assert not r["accepted"] and "proof" in r["reason"]
    proof = ledger.add_evidence("compare_fields", {}, compare_fields_impl(pkg("S14"), "Revenue", "Revenue"))
    r = ledger.record(check="FX-01", severity="BLOCKER", status="PASS", title="Revenue identical", explanation="canonical",
                      evidence_ids=[proof], thoughtspot_object="Sales Orders::Revenue", strategy_object="Sales Orders::Revenue")
    assert r["accepted"]
    # a proof for a different pair does not count
    r = ledger.record(check="FX-01", severity="BLOCKER", status="PASS", title="x", explanation="x", evidence_ids=[proof],
                      thoughtspot_object="Sales Orders::Net Revenue", strategy_object="Sales Orders::Net Revenue")
    assert not r["accepted"]


def test_rule_fail_can_only_be_softened_with_data():
    ledger, sweep_id, out = ledger_with_sweep("S07")
    lead = out["open_leads"][0]["lead_id"]
    r = ledger.record(check="AG-01", severity="WARNING", status="FAIL", title="minor", explanation="x",
                      evidence_ids=[sweep_id], resolves_leads=[lead])
    assert not r["accepted"] and "data" in r["reason"]
    data = ledger.add_evidence("request_data_probe", {}, {"match": True, "subject": {"lead": lead}})
    r = ledger.record(check="AG-01", severity="WARNING", status="FAIL", title="minor", explanation="x",
                      evidence_ids=[sweep_id, data], resolves_leads=[lead])
    assert r["accepted"]


def test_finalize_requires_sweep_and_resolved_leads():
    ledger = Ledger(package=pkg("S07"))
    assert not ledger.finalize("s")["accepted"]
    ledger, sweep_id, out = ledger_with_sweep("S07")
    refused = ledger.finalize("s")
    assert not refused["accepted"] and refused["open_leads"]
    lead = out["open_leads"][0]["lead_id"]
    assert ledger.record(check="AG-01", severity="BLOCKER", status="FAIL", title="Customer Count counts duplicates",
                         explanation="COUNT vs COUNT DISTINCT", evidence_ids=[sweep_id], resolves_leads=[lead],
                         thoughtspot_object="Sales Orders::Customer Count")["accepted"]
    done = ledger.finalize("Customer Count must use COUNT DISTINCT.")
    assert done["accepted"] and done["verdict"] == "NOT_READY"
    report = ledger.report
    assert report.summary.startswith("Customer Count")
    agent_finding = next(f for f in report.findings if f.origin == "agent")
    assert agent_finding.thoughtspot.location  # carried over from the lead
    assert sum(f.status == "PASS" for f in report.findings) == len(out["proven_by_rules"])


def test_forced_finalize_keeps_unreviewed_leads():
    ledger, _, _ = ledger_with_sweep("S05")
    assert ledger.finalize("stopped", force=True)["accepted"]
    unreviewed = [f for f in ledger.report.findings if "not reviewed" in f.evidence.get("note", "")]
    assert unreviewed and ledger.report.verdict == "NOT_READY"


def test_data_proof_must_be_about_the_same_subject():
    ledger, sweep_id, _ = ledger_with_sweep("S14")
    other = ledger.add_evidence("request_data_probe", {}, {"match": True, "subject": {"thoughtspot": "Revenue"}})
    r = ledger.record(check="FX-01", severity="BLOCKER", status="PASS", title="x", explanation="x",
                      evidence_ids=[sweep_id, other], thoughtspot_object="Sales Orders::Net Revenue",
                      strategy_object="Sales Orders::Net Revenue")
    assert not r["accepted"]
    same = ledger.add_evidence("request_data_probe", {}, {"match": True, "subject": {"thoughtspot": "Net Revenue"}})
    r = ledger.record(check="FX-01", severity="BLOCKER", status="PASS", title="x", explanation="x",
                      evidence_ids=[same], thoughtspot_object="Sales Orders::Net Revenue",
                      strategy_object="Sales Orders::Net Revenue")
    assert r["accepted"]
