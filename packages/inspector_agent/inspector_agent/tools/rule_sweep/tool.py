"""Tool: run_rule_sweep. The deterministic checks as a fast first pass: they produce leads, not verdicts."""

from __future__ import annotations

import functools

from strands import ToolContext, tool

from ..findings_ledger.report import Finding, finalize
from ..package_inventory.loader import load_cached, location_of, package_key
from . import checks
from .context import build


@functools.lru_cache(maxsize=32)
def _sweep(location: str, key: str) -> tuple[Finding, ...]:
    pkg = load_cached(location)
    if pkg.errors:
        return tuple(finalize([Finding(check="PK-01", severity="BLOCKER", status="FAIL",
                                       title="The migration package could not be read completely",
                                       evidence={"errors": "; ".join(i.message for i in pkg.errors)})]))
    found = finalize(checks.run_all(build(pkg)))
    for i, f in enumerate(found, 1):
        f.id = f"L{i:02d}"
    return tuple(found)


def sweep(package: str) -> list[Finding]:
    """All leads, PASS ones included, with stable ids L01, L02 ... (copies: callers may modify them)."""
    location = location_of(package)
    return [f.model_copy(deep=True) for f in _sweep(location, package_key(location))]


def _lead(f: Finding) -> dict:
    out = {"lead_id": f.id, "check": f.check, "severity": f.severity, "status": f.status, "title": f.title}
    if f.thoughtspot:
        out["thoughtspot"] = f"{f.thoughtspot.model}::{f.thoughtspot.name}"
    if f.strategy:
        out["strategy"] = f"{f.strategy.model}::{f.strategy.name}"
    for key in ("symptoms", "needs"):
        if getattr(f, key):
            out[key] = getattr(f, key)
    if f.evidence:
        out["evidence"] = {k: v for k, v in f.evidence.items() if k != "data_probes"}  # summaries, not raw results
    if f.decided_by == "rule+data":
        out["settled_by_data"] = True
    if f.latent:
        out["latent"] = True
    return out


def leads_view(leads: list[Finding]) -> dict:
    """What the agent sees of a sweep: open leads in full, proven items by name."""
    open_leads = [f for f in leads if f.status != "PASS"]
    proven = [f for f in leads if f.status == "PASS"]
    return {
        "open_leads": [_lead(f) for f in open_leads],
        "proven_by_rules": [f"{f.id} {f.check} {(f.thoughtspot or f.strategy).name}"
                            + (" (by data)" if f.decided_by == "rule+data" else "") for f in proven],
        "summary": f"{len(open_leads)} open leads, {len(proven)} items proven by rules",
    }


def run_rule_sweep_impl(package: str) -> dict:
    return leads_view(sweep(package))


@tool(context=True)
def run_rule_sweep(package: str, tool_context: ToolContext) -> dict:
    """Run every deterministic check (coverage, attribute lineage, measure definitions, joins, fan-out/chasm,
    model filters, dashboards: queries, default filters, top-N, titles) as a quick first pass. Returns OPEN LEADS
    to investigate (FAIL or UNVERIFIED, with the rule's reasoning) and the items the rules already proved identical. Leads are not verdicts: confirm, refine, group
    by root cause or overturn them with evidence, then record findings that resolve them. In a validation run the
    leads' data probes have already run: leads marked settled_by_data are decided and cannot be overturned.

    Args:
        package: Path of the package folder or zip.
    """
    ledger = (tool_context.invocation_state or {}).get("ledger")
    if ledger is not None and ledger.sweep_ran:
        return leads_view(list(ledger.leads.values()))
    return run_rule_sweep_impl(package)
