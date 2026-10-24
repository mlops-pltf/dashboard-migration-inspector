"""inspector_agent: validates a migration package, an A2A server (port 9000) on Bedrock AgentCore.

Technology-neutral: the ThoughtSpot and Strategy expert agents export their side as IR (over A2A); the Inspector
investigates with its tools, records findings under the ledger's evidence rules, and returns the report.

Structured skill (answered by code; the agent runs inside it):
  validate {package, mode: "agent" | "rules" | "rules+data", max_turns} -> report JSON + markdown
Conversation: any text message goes to the Inspector agent and its tools.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from strands import Agent
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from inspector_contract.a2a import SkillRouterExecutor, agent_card

from .llm import get_model
from .tools.counterpart_finder.tool import find_counterparts, match_fields
from .tools.data_probe.settle import settle
from .tools.data_probe.tool import request_data_probe
from .tools.definition_comparer.tool import compare_fields
from .tools.expert_client.tool import analyze_sql, ask_expert
from .tools.filter_inspector.tool import compare_filters
from .tools.findings_ledger import Ledger, Report, markdown
from .tools.findings_ledger.report import counts_of, finalize, verdict_of
from .tools.findings_ledger.tool import finalize_report, record_finding
from .tools.join_analyzer.tool import analyze_row_multiplication, compare_joins, get_join_graph
from .tools.lineage_tracer.tool import trace_lineage
from .tools.model_browser.tool import get_object, list_fields
from .tools.package_inventory.loader import load_cached, location_of
from .tools.package_inventory.tool import inspect_package
from .tools.query_compiler.tool import compile_query
from .tools.rule_sweep.tool import run_rule_sweep, sweep

NAME = "inspector_agent"
DESCRIPTION = "Inspector: validates that a Strategy migration reproduces every ThoughtSpot number, with evidence."
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()

TOOLS = [
    inspect_package, run_rule_sweep, match_fields, find_counterparts, list_fields, get_object, trace_lineage,
    compare_fields, get_join_graph, compare_joins, analyze_row_multiplication, compare_filters, ask_expert,
    analyze_sql, compile_query, request_data_probe, record_finding, finalize_report,
]
LEDGER_TOOLS = {"record_finding", "finalize_report"}


class EvidenceHook(HookProvider):
    """Registers every successful analysis-tool result in the run's ledger and tags it with an evidence id."""

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        ledger = (event.invocation_state or {}).get("ledger")
        name = (event.tool_use or {}).get("name")
        result = event.result or {}
        if ledger is None or name in LEDGER_TOOLS or result.get("status") != "success":
            return
        eid = ledger.add_evidence(name, event.tool_use.get("input") or {}, _payload(result))
        event.result = {**result, "content": [*result.get("content", []), {"text": f"evidence_id: {eid}"}]}


def _payload(result: dict):
    for block in result.get("content", []):
        if "json" in block:
            return block["json"]
        if "text" in block:
            try:
                return json.loads(block["text"])
            except (TypeError, ValueError):
                return block["text"]
    return None


def build_agent(model=None) -> Agent:
    return Agent(model=model or get_model("strong"), tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                 hooks=[EvidenceHook()], callback_handler=None, name=NAME)


def run_validation(package: str, model=None, max_turns: int = 60) -> Report:
    location = location_of(package)
    load_cached(location, refresh=True)  # fresh exports from the experts for this run
    ledger = Ledger(package=location, run_id=f"R-{uuid.uuid4().hex[:8]}")
    agent = build_agent(model)
    prompt = (f"Validate the migration package at: {location}\n"
              "Use this exact location as the `package` argument of every tool.")
    info = {"model": getattr(agent.model, "config", {}).get("model_id", "custom"), "max_turns": max_turns}
    text = ""
    try:
        result = agent(prompt, invocation_state={"ledger": ledger}, limits={"turns": max_turns})
        text = str(result)
        usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
        if usage:
            info["tokens"] = dict(usage)
    except Exception as exc:  # budget exhausted or model failure: keep what was established
        info["stopped"] = f"{type(exc).__name__}: {exc}"
    if ledger.report is None:
        ledger.finalize(summary=text or "The agent did not finish; open leads are reported as the rules left them.",
                        force=True, agent_info=info)
    else:
        ledger.report.agent.update(info)
    return ledger.report


def run_rules_only(package: str, with_data: bool = False) -> Report:
    """No LLM in the Inspector: the rule sweep's leads become the report (experts are still asked for IR).
    ``with_data``: the leads' data probes run through the data agent and settle what they can."""
    location = location_of(package)
    load_cached(location, refresh=True)
    leads = sweep(location)
    if with_data:
        leads = settle(location, leads)
    for f in leads:
        f.resolves = [f.id]
    findings = finalize(leads)
    return Report(run_id=f"R-{uuid.uuid4().hex[:8]}", package_id=load_cached(location).id,
                  verdict=verdict_of(findings), counts=counts_of(findings), findings=findings,
                  stage="rules+data" if with_data else "rules-only")


# ---------------------------------------------------------------------------------------------
# A2A server
# ---------------------------------------------------------------------------------------------
def _validate_skill(args: dict) -> dict:
    package = str(args.get("package") or args.get("location") or "")
    mode = args.get("mode", "agent")
    report = run_rules_only(package, with_data=mode == "rules+data") if mode in ("rules", "rules+data") else \
        run_validation(package, max_turns=int(args.get("max_turns", 60)))
    return {"run_id": report.run_id, "verdict": report.verdict, "counts": report.counts, "summary": report.summary,
            "report": report.model_dump(mode="json"), "markdown": markdown(report)}


SKILLS = {"validate": _validate_skill}
SKILL_DOCS = {"validate": "Validate a migration package {package, mode: agent|rules|rules+data, max_turns}: report with verdict, "
                          "findings and evidence."}


def executor(conversation: bool = True) -> SkillRouterExecutor:
    chat = StrandsA2AExecutor(agent_factory=lambda _context_id: build_agent()) if conversation else None
    return SkillRouterExecutor(SKILLS, chat)


def card(url: str):
    return agent_card(NAME, DESCRIPTION, url, SKILL_DOCS,
                      conversation="Ask the Inspector to look into a package: matches, definitions, joins, findings.")


def app(url: str = "http://localhost:9000/", conversation: bool = True):
    from bedrock_agentcore.runtime import build_a2a_app

    return build_a2a_app(executor(conversation), card(url), runtime_url=url)


def main() -> None:
    from bedrock_agentcore.runtime import serve_a2a

    serve_a2a(executor(), card(os.environ.get("AGENTCORE_RUNTIME_URL", "http://localhost:9000/")))


if __name__ == "__main__":
    main()
