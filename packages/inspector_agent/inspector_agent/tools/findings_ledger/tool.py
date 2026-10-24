"""Tools: record_finding, finalize_report. They act on the run's Ledger, passed by the runtime in
invocation_state["ledger"]."""

from __future__ import annotations

from strands import ToolContext, tool

from .ledger import Ledger


def _ledger(tool_context: ToolContext) -> Ledger:
    ledger = tool_context.invocation_state.get("ledger")
    if ledger is None:
        raise RuntimeError("no ledger in invocation_state")
    return ledger


@tool(context=True)
def record_finding(check: str, severity: str, status: str, title: str, explanation: str, evidence_ids: list[str],
                   tool_context: ToolContext, thoughtspot_object: str | None = None,
                   strategy_object: str | None = None, resolves_leads: list[str] | None = None,
                   symptoms: list[str] | None = None, needs: list[str] | None = None,
                   suggested_fix: str | None = None, latent: bool = False) -> dict:
    """Record one finding in the report. One finding per root cause: list the other affected objects as symptoms.

    Rules enforced here (a rejected call explains why):
    - cite at least one evidence id (E1, E2 ... returned with every analysis tool result);
    - a BLOCKER PASS needs proof: compare_fields identical=true for this pair, an item proven by the rule sweep,
      or a data probe on the same subject with match=true; otherwise record UNVERIFIED and say in `needs` what would settle it;
    - a lead the rules FAILED can only be softened (PASS, UNVERIFIED, lower severity) with data evidence;
    - list in resolves_leads every rule-sweep lead (L01, L02 ...) this finding settles.

    Args:
        check: Check id from the catalogue, e.g. JN-04, AG-02, FX-01.
        severity: BLOCKER (can change a number), WARNING, LOW (cosmetic) or INFO.
        status: FAIL, PASS or UNVERIFIED.
        title: One line a developer understands.
        explanation: Why, in two or three sentences, referring to the evidence.
        evidence_ids: Evidence ids supporting this finding.
        thoughtspot_object: "Model::Object" on the ThoughtSpot side, if any.
        strategy_object: "Model::Object" on the Strategy side, if any.
        resolves_leads: Rule-sweep lead ids settled by this finding.
        symptoms: Other objects affected by the same root cause.
        needs: What would settle an UNVERIFIED finding, e.g. data:kpi_equivalence.
        suggested_fix: What the developer should change.
        latent: True for a real definition difference that data shows has no impact today (still a FAIL).
    """
    return _ledger(tool_context).record(
        check=check, severity=severity, status=status, title=title, explanation=explanation,
        evidence_ids=evidence_ids, thoughtspot_object=thoughtspot_object, strategy_object=strategy_object,
        resolves_leads=resolves_leads, symptoms=symptoms, needs=needs, suggested_fix=suggested_fix,
        latent=latent)


@tool(context=True)
def finalize_report(summary: str, tool_context: ToolContext) -> dict:
    """Close the run and build the report. Refused while rule-sweep leads are still open. The verdict
    (READY / NOT_READY / NEEDS_REVIEW) is computed from the recorded findings, not chosen.

    Args:
        summary: Executive summary for the developer: verdict drivers, root causes, what to fix first.
    """
    return _ledger(tool_context).finalize(summary)
