"""The full agent run (run_validation) driven by scripted models instead of an LLM: real tools, real ledger, real
experts in-process. Covers a diligent model and the ways a real one misbehaves: unknown tools, bad arguments,
verdict claims, a failing provider, a run that never ends. Whatever the model does, the run returns a report
whose verdict comes from the ledger."""

import itertools
import json
from typing import Any

import pytest
from strands.models.model import Model

from conftest import needs_synthetic, pkg
from inspector_agent.inspector_agent import run_rules_only, run_validation

pytestmark = needs_synthetic


class Scripted(Model):
    """A Strands model whose turns come from `policy(messages)`: a list of ("tool", name, input) / ("text", str),
    or an exception to raise (a provider failure)."""

    def __init__(self, policy):
        self.policy, self.ids = policy, itertools.count(1)

    def update_config(self, **_):
        pass

    def get_config(self):
        return {"model_id": "scripted"}

    async def structured_output(self, *_, **__):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **_: Any):
        turn = self.policy(messages)
        if isinstance(turn, Exception):
            raise turn
        yield {"messageStart": {"role": "assistant"}}
        uses_tools = False
        for block in turn:
            if block[0] == "tool":
                uses_tools = True
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{next(self.ids)}", "name": block[1]}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(block[2])}}}}
            else:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": block[1]}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if uses_tools else "end_turn"}}


def _results(messages) -> list[tuple[dict | str, str | None]]:
    """(payload, evidence id) of every tool result so far."""
    out = []
    for m in messages:
        for c in m.get("content", []):
            if "toolResult" in c:
                texts = [b.get("text") for b in c["toolResult"].get("content", []) if "text" in b]
                eid = next((t.split(": ")[1] for t in texts if t and t.startswith("evidence_id: ")), None)
                payload = next((b["json"] for b in c["toolResult"]["content"] if "json" in b), None)
                if payload is None and texts:
                    try:
                        payload = json.loads(texts[0])
                    except ValueError:
                        payload = texts[0]
                out.append((payload, eid))
    return out


def _turn(messages) -> int:
    return sum(1 for m in messages if m["role"] == "assistant")


def diligent(package):
    """Sweep, record one finding per open lead (as the rules left it), finalize."""
    def policy(messages):
        n = _turn(messages)
        if n == 0:
            return [("tool", "run_rule_sweep", {"package": package})]
        if n == 1:
            sweep, eid = _results(messages)[-1]
            return [("tool", "record_finding", {
                "check": lead["check"], "severity": lead["severity"], "status": lead["status"], "title": lead["title"],
                "explanation": "As the rule sweep showed.", "evidence_ids": [eid], "resolves_leads": [lead["lead_id"]]})
                for lead in sweep["open_leads"]] or [("text", "nothing open")]
        if n == 2:
            return [("tool", "finalize_report", {"summary": "One root cause; see findings."})]
        return [("text", "Done.")]
    return policy


def messy(package):
    """Calls a tool that does not exist, a tool with missing arguments, claims a verdict and stops."""
    def policy(messages):
        n = _turn(messages)
        if n == 0:
            return [("tool", "does_not_exist", {"x": 1})]
        if n == 1:
            return [("tool", "record_finding", {"check": "JN-04"})]
        if n == 2:
            return [("tool", "run_rule_sweep", {"package": package}), ("tool", "finalize_report", {"summary": "x"})]
        return [("text", "Verdict: READY. Everything matches.")]
    return policy


def failing(package):
    """The provider fails after the first turn (throttling, an outage)."""
    def policy(messages):
        if _turn(messages) == 0:
            return [("tool", "run_rule_sweep", {"package": package})]
        return RuntimeError("ThrottlingException: rate exceeded")
    return policy


def endless(package):
    def policy(messages):
        return [("tool", "run_rule_sweep", {"package": package})]
    return policy


@pytest.fixture(scope="module")
def rules_verdict():
    return {sid: run_rules_only(pkg(sid), with_data=True).verdict for sid in ("S01", "S07")}


@pytest.mark.parametrize("sid", ["S01", "S07"])
def test_diligent_model_reaches_the_rules_verdict(sid, rules_verdict):
    report = run_validation(pkg(sid), model=Scripted(diligent(pkg(sid))))
    assert report.stage == "agent" and report.agent["unreviewed_leads"] == 0
    assert report.verdict == rules_verdict[sid]
    assert "stopped" not in report.agent


@pytest.mark.parametrize("policy", [messy, failing, endless])
def test_misbehaving_model_still_gives_the_rules_verdict(policy, rules_verdict):
    report = run_validation(pkg("S07"), model=Scripted(policy(pkg("S07"))), max_turns=6)
    assert report.verdict == rules_verdict["S07"]  # unreviewed leads keep the rules' status
    assert report.findings and report.agent["unreviewed_leads"] >= 1
    assert "READY. Everything" not in report.summary
    if policy is failing:
        assert "Throttling" in report.agent["stopped"]
