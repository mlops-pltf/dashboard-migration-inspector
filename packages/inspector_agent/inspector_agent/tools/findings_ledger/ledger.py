"""The run's evidence ledger and the rules for recording findings.

The agent reasons freely; the ledger makes sure a verdict can be trusted:
  * every analysis tool result is evidence with an id (E1, E2 ...), registered by the agent runtime
  * a finding must cite evidence
  * a BLOCKER PASS needs a proof: an identical compare_fields result for the same pair, an item the rule sweep
    proved, or a data probe that matched for the same subject
  * turning a rule-sweep FAIL into anything softer needs data evidence
  * a lead the data already settled before the run (``preload``) keeps its status and severity: the agent can
    explain it, group symptoms under it and suggest a fix, but not overturn it
  * the report can only be finalised once every open lead from the rule sweep is resolved
The verdict (READY / NOT_READY / NEEDS_REVIEW) is computed here, never chosen by the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .report import Finding, ObjectRef, Report, counts_of, finalize, verdict_of


@dataclass
class Evidence:
    id: str
    tool: str
    input: dict
    output: Any


def _name(ref: str | None) -> str | None:
    return ref.rpartition("::")[2].strip() if ref else None


def _model(ref: str | None) -> str | None:
    return ref.rpartition("::")[0].strip() or None if ref and "::" in ref else None


@dataclass
class Ledger:
    package: str
    run_id: str = ""
    evidence: dict[str, Evidence] = field(default_factory=dict)
    leads: dict[str, Finding] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    addressed: set[str] = field(default_factory=set)
    report: Report | None = None
    sweep_ran: bool = False

    # -- evidence -----------------------------------------------------------------------------
    def add_evidence(self, tool: str, tool_input: dict, output: Any) -> str:
        eid = f"E{len(self.evidence) + 1}"
        self.evidence[eid] = Evidence(eid, tool, dict(tool_input or {}), output)
        if tool == "run_rule_sweep" and not self.sweep_ran:
            from ..rule_sweep.tool import sweep

            self.leads = {f.id: f for f in sweep(tool_input.get("package") or self.package)}
            self.sweep_ran = True
        return eid

    def _cited(self, ids: list[str]) -> list[Evidence]:
        return [self.evidence[i] for i in ids if i in self.evidence]

    def _rule_proof(self, cited: list[Evidence], ts: str | None, st: str | None) -> bool:
        for e in cited:
            out = e.output if isinstance(e.output, dict) else {}
            if e.tool == "compare_fields" and out.get("identical") is True \
                    and out.get("thoughtspot") == ts and out.get("strategy") == st:
                return True
            if e.tool == "run_rule_sweep":
                for lead in self.leads.values():
                    if lead.status == "PASS" and lead.thoughtspot and lead.thoughtspot.name == ts \
                            and (st is None or (lead.strategy and lead.strategy.name == st)):
                        return True
        return False

    def preload(self, leads: list[Finding]) -> str:
        """Start the run from leads the code already swept and settled with data. Registered as evidence (the
        rule sweep's result), so findings can cite it; returns that evidence id."""
        from ..rule_sweep.tool import leads_view

        self.leads = {f.id: f for f in leads}
        self.sweep_ran = True
        eid = f"E{len(self.evidence) + 1}"
        self.evidence[eid] = Evidence(eid, "run_rule_sweep", {"package": self.package}, leads_view(leads))
        return eid

    @staticmethod
    def _data_proof(cited: list[Evidence], ts: str | None, st: str | None, leads: list[str]) -> bool:
        """A matching data probe about this finding's subject: the same ThoughtSpot object (field or join), the
        same Strategy object, or a lead the finding resolves."""
        for e in cited:
            out = e.output if isinstance(e.output, dict) else {}
            if e.tool != "request_data_probe" or out.get("match") is not True:
                continue
            subject = out.get("subject") or {}
            if (ts and ts in (subject.get("thoughtspot"), subject.get("join"))) \
                    or (st and st == subject.get("strategy")) or subject.get("lead") in leads:
                return True
        return False

    # -- findings -----------------------------------------------------------------------------
    def record(self, *, check: str, severity: str, status: str, title: str, explanation: str,
               evidence_ids: list[str], thoughtspot_object: str | None = None, strategy_object: str | None = None,
               resolves_leads: list[str] | None = None, symptoms: list[str] | None = None,
               needs: list[str] | None = None, suggested_fix: str | None = None, latent: bool = False) -> dict:
        resolves_leads = list(resolves_leads or [])
        severity, status = severity.upper(), status.upper()
        if severity not in ("BLOCKER", "WARNING", "LOW", "INFO") or status not in ("FAIL", "PASS", "UNVERIFIED"):
            return {"accepted": False, "reason": "severity must be BLOCKER/WARNING/LOW/INFO, status FAIL/PASS/UNVERIFIED"}
        unknown = [i for i in evidence_ids if i not in self.evidence]
        if not evidence_ids or unknown:
            return {"accepted": False, "reason": f"cite existing evidence ids (unknown: {unknown or 'none given'})"}
        bad_leads = [i for i in resolves_leads if i not in self.leads]
        if bad_leads:
            return {"accepted": False, "reason": f"unknown lead ids {bad_leads}"}

        settled = [self.leads[i] for i in resolves_leads if self.leads[i].decided_by == "rule+data"
                   and (status != self.leads[i].status or severity != self.leads[i].severity)]
        if settled:
            return {"accepted": False, "reason": "; ".join(
                f"{l.id} was settled by data before the run ({l.status} {l.severity}: {l.title})" for l in settled)
                + ". Keep that status and severity: add the explanation, symptoms and suggested fix."}

        cited = self._cited(evidence_ids)
        ts, st = _name(thoughtspot_object), _name(strategy_object)
        data = self._data_proof(cited, ts, st, resolves_leads)
        hard_leads = [self.leads[i] for i in resolves_leads
                      if self.leads[i].severity == "BLOCKER" and self.leads[i].status == "FAIL"]
        if hard_leads and (status != "FAIL" or severity != "BLOCKER") and not data:
            return {"accepted": False, "reason": f"{', '.join(l.id for l in hard_leads)} failed a rule; only data "
                                                 "evidence (a matching data probe) can soften that. Record FAIL, or "
                                                 "request a data probe."}
        if status == "PASS" and severity == "BLOCKER" and not (data or self._rule_proof(cited, ts, st)):
            return {"accepted": False, "reason": "a BLOCKER PASS needs proof: cite compare_fields with identical=true "
                                                 "for this pair, an item the rule sweep proved, or a matching data probe. "
                                                 "Otherwise record UNVERIFIED with what would settle it."}

        decided = "agent+data" if data else "agent"
        f = Finding(check=check, severity=severity, status=status, title=title,
                    thoughtspot=ObjectRef(model=_model(thoughtspot_object), name=ts) if ts else None,
                    strategy=ObjectRef(model=_model(strategy_object), name=st) if st else None,
                    decided_by=decided, evidence={"explanation": explanation}, symptoms=list(symptoms or []),
                    needs=list(needs or []), suggested_fix=suggested_fix, origin="agent", latent=latent,
                    evidence_refs=list(evidence_ids), resolves=resolves_leads)
        self._enrich(f)
        self.findings.append(f)
        self.addressed.update(resolves_leads)
        return {"accepted": True, "finding_number": len(self.findings),
                "open_leads_left": [i for i in self.open_leads()]}

    def _enrich(self, f: Finding) -> None:
        """Carry locations/definitions over from the leads it resolves, so the report keeps file:line refs."""
        for lid in f.resolves:
            lead = self.leads[lid]
            for side in ("thoughtspot", "strategy"):
                mine, theirs = getattr(f, side), getattr(lead, side)
                if mine and theirs and mine.name == theirs.name:
                    setattr(f, side, mine.model_copy(update={"model": mine.model or theirs.model,
                                                             "location": theirs.location,
                                                             "definition": theirs.definition, "kind": theirs.kind}))
                elif mine is None and theirs is not None:
                    setattr(f, side, theirs)

    def open_leads(self) -> list[str]:
        return [i for i, f in self.leads.items() if f.status != "PASS" and i not in self.addressed]

    # -- report -------------------------------------------------------------------------------
    def finalize(self, summary: str, force: bool = False, agent_info: dict | None = None) -> dict:
        if not self.sweep_ran and not force:
            return {"accepted": False, "reason": "run_rule_sweep first: it is the coverage baseline"}
        left = self.open_leads()
        if left and not force:
            return {"accepted": False, "reason": "resolve every open lead first (record a finding that lists it in "
                                                 "resolves_leads)", "open_leads": [
                f"{i} {self.leads[i].check} {self.leads[i].status}: {self.leads[i].title}" for i in left]}
        proven = [f.model_copy(deep=True) for i, f in self.leads.items() if f.status == "PASS" and i not in self.addressed]
        unreviewed = []
        for i in left:
            f = self.leads[i].model_copy(deep=True)
            f.evidence["note"] = ("settled by data before the agent ran; not reviewed further" if f.decided_by == "rule+data"
                                  else "not reviewed by the agent (run ended first); rule result kept as is")
            unreviewed.append(f)
        for f in proven + unreviewed:
            f.resolves = [f.id]
        findings = finalize([f.model_copy(deep=True) for f in self.findings] + proven + unreviewed)
        verdict = verdict_of(findings)
        summary, removed = without_verdict_claims(summary, verdict)
        self.report = Report(
            run_id=self.run_id, package_id=self.package.rstrip("/").rpartition("/")[2], verdict=verdict,
            counts=counts_of(findings), findings=findings, stage="agent", summary=summary,
            agent={**(agent_info or {}), "evidence_items": len(self.evidence), "unreviewed_leads": len(unreviewed),
                   **({"summary_lines_removed": removed} if removed else {})})
        return {"accepted": True, "verdict": self.report.verdict, "counts": self.report.counts}


_VERDICTS = re.compile(r"\b(NOT[_ ]READY|NEEDS[_ ]REVIEW|READY)\b")


def without_verdict_claims(summary: str, verdict: str) -> tuple[str, int]:
    """The verdict is computed, so the summary may not state one: drop lines that mention a verdict or name one
    other than the computed verdict. Returns the cleaned summary and how many lines were dropped."""
    kept, removed = [], 0
    for line in summary.splitlines():
        named = {m.replace(" ", "_") for m in _VERDICTS.findall(line)}
        if re.search(r"\bverdict\b", line, re.IGNORECASE) or named - {verdict}:
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed
