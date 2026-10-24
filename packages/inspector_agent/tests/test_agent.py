"""Inspector wiring (no model calls): tools, prompt, evidence hook, rules-only mode, A2A server."""

import json
import shutil
from types import SimpleNamespace

import httpx

from conftest import PACKAGES, needs_synthetic, pkg
from inspector_agent import inspector_agent as ia
from inspector_agent.tools.findings_ledger import Ledger
from inspector_agent.tools.rule_sweep.tool import run_rule_sweep_impl
from inspector_contract.a2a import A2AClient

pytestmark = needs_synthetic


def test_agent_builds_with_all_tools_and_a_neutral_prompt():
    agent = ia.build_agent()
    assert set(agent.tool_names) == {t.tool_name for t in ia.TOOLS}
    assert "No pass without proof" in ia.SYSTEM_PROMPT and "ask_expert" in ia.SYSTEM_PROMPT
    assert "join_progressive" not in ia.SYSTEM_PROMPT  # tool specifics live with the experts


def test_evidence_hook_tags_results_and_fills_the_ledger():
    ledger = Ledger(package=pkg("S07"))
    out = run_rule_sweep_impl(pkg("S07"))
    event = SimpleNamespace(invocation_state={"ledger": ledger},
                            tool_use={"name": "run_rule_sweep", "input": {"package": pkg("S07")}, "toolUseId": "t1"},
                            result={"toolUseId": "t1", "status": "success", "content": [{"text": json.dumps(out)}]})
    ia.EvidenceHook().after_tool(event)
    assert event.result["content"][-1] == {"text": "evidence_id: E1"}
    assert ledger.sweep_ran and ledger.open_leads()
    event = SimpleNamespace(invocation_state={"ledger": ledger}, tool_use={"name": "record_finding", "input": {}},
                            result={"status": "success", "content": [{"text": "{}"}]})
    ia.EvidenceHook().after_tool(event)
    assert len(ledger.evidence) == 1


def test_rules_only_mode():
    report = ia.run_rules_only(pkg("S13"))
    assert report.stage == "rules-only" and report.verdict == "NOT_READY" and report.package_id == "S13"
    assert {f.check for f in report.findings if f.blocking} == {"JN-01", "JN-05"}


def test_a2a_validate_skill_and_path_guard(monkeypatch, tmp_path):
    shutil.copytree(PACKAGES / "S07", tmp_path / "S07")
    monkeypatch.setenv("PACKAGE_ROOT", str(tmp_path))
    client = A2AClient("http://inspector/", transport=httpx.ASGITransport(app=ia.app(conversation=False)))
    assert {s.id for s in client.card().skills} == {"validate", "ask"}
    out = client.call("validate", {"package": "S07", "mode": "rules"})
    assert out["verdict"] == "NOT_READY" and "AG-01" in out["markdown"]
    assert "error" in client.call("validate", {"package": "../../etc", "mode": "rules"})
